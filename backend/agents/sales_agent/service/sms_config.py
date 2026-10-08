"""Sales Agent service layer: SMS config: sending channel CRUD, registered templates, secret encryption/decryption, effective config.

Structured to mirror `email_config.py`; credential encryption/scope/account selection all delegate to `channel_common`.
SMS adds only two things over email, which are also why this file exists:

1. **`content_mode`**: CN channels (Tencent Cloud/Aliyun) can only send platform-approved templates + variable slots;
   free copy cannot be sent; only international channels (Twilio) can send arbitrary text. Hence an extra template table and template CRUD.
2. **`region_scope`**: choose the channel by the recipient number's country code; sending a US number with a CN signature always fails.
   This provides the candidate set; actual routing is in `sms_send.py`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from agents.sales_agent.models import (
    SalesAgentSmsAccountORM,
    SalesAgentSmsDeliveryORM,
    SalesAgentSmsSettingORM,
    SalesAgentSmsTemplateORM,
)
from agents.sales_agent.service.sms_providers import provider_supports_callback

from agents.sales_agent.service.common import SalesAgentError, _dumps, _iso, _loads
from agents.sales_agent.service.reports import _sales_agent_public_base_url
from agents.sales_agent.service.channel_common import (
    CHANNEL_SENDER_SELECTION_POLICIES,
    channel_account_scope_query,
    channel_callback_token,
    channel_scope_user_id,
    decrypt_channel_secret,
    encrypt_channel_secret,
    normalize_channel_scope,
    normalize_channel_sender_policy,
    pick_channel_sender_account,
    sanitize_channel_provider_config,
)

SMS_CONTENT_MODES = {"template", "freeform"}
SMS_REGION_SCOPES = {"cn", "intl", "all"}
SMS_TEMPLATE_PURPOSES = {"outreach", "followup", "test"}
SMS_TEMPLATE_APPROVAL_STATUSES = {"pending", "approved", "rejected"}
SALES_AGENT_SMS_STATUSES = {"disabled", "no_recipient", "rejected", "sending", "sent", "delivered", "failed"}

SALES_AGENT_SMS_PROVIDER_OPTIONS = [
    {"provider": "tencent_cloud", "label": "腾讯云短信", "content_mode": "template", "region_scope": "cn", "available": True},
    {"provider": "aliyun", "label": "阿里云短信", "content_mode": "template", "region_scope": "cn", "available": True},
    {"provider": "twilio", "label": "Twilio", "content_mode": "freeform", "region_scope": "intl", "available": True},
    {"provider": "aws_sns", "label": "AWS SNS", "content_mode": "freeform", "region_scope": "intl", "available": False},
    {"provider": "custom_http", "label": "自定义 HTTP 网关", "content_mode": "freeform", "region_scope": "all", "available": False},
]

# Platforms name their credentials very differently (SecretId / AccessKeyId / Account SID); a wrong label is the most common
# "test send failed". As with the email presets, what each platform needs and where to get it is written right next to the form.
SALES_AGENT_SMS_PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "key": "tencent_cloud",
        "provider": "tencent_cloud",
        "label": "腾讯云短信",
        "content_mode": "template",
        "region_scope": "cn",
        "needs_sign_name": True,
        "needs_sdk_app_id": True,
        "needs_from_number": False,
        "key_id_label": "SecretId",
        "secret_label": "SecretKey",
        "sdk_app_id_label": "短信应用 SmsSdkAppId",
        "hint": "在腾讯云控制台「访问管理 → API 密钥管理」拿 SecretId/SecretKey;签名与正文模板都要先在短信控制台报备通过(审核约 2 小时~1 个工作日),之后只能改变量,不能改正文。",
        "help_url": "https://cloud.tencent.com/document/product/382",
    },
    {
        "key": "aliyun",
        "provider": "aliyun",
        "label": "阿里云短信",
        "content_mode": "template",
        "region_scope": "cn",
        "needs_sign_name": True,
        "needs_sdk_app_id": False,
        "needs_from_number": False,
        "key_id_label": "AccessKeyId",
        "secret_label": "AccessKeySecret",
        "sdk_app_id_label": "",
        "hint": "在阿里云 RAM 控制台创建 AccessKey;签名与模板需先在短信服务控制台报备通过,之后只能改变量。",
        "help_url": "https://help.aliyun.com/zh/sms/",
    },
    {
        "key": "twilio",
        "provider": "twilio",
        "label": "Twilio(国际)",
        "content_mode": "freeform",
        "region_scope": "intl",
        "needs_sign_name": False,
        "needs_sdk_app_id": False,
        "needs_from_number": True,
        "key_id_label": "Account SID",
        "secret_label": "Auth Token",
        "sdk_app_id_label": "",
        "hint": "在 Twilio Console 首页拿 Account SID / Auth Token;主叫号填已购买的号码(E.164,如 +15551234567)。国际通道可发自由文案,不需要报备模板。",
        "help_url": "https://www.twilio.com/docs/messaging",
    },
    {
        "key": "aws_sns",
        "provider": "aws_sns",
        "label": "AWS SNS(国际)",
        "content_mode": "freeform",
        "region_scope": "intl",
        "needs_sign_name": False,
        "needs_sdk_app_id": False,
        "needs_from_number": False,
        "key_id_label": "AccessKeyId",
        "secret_label": "SecretAccessKey",
        "sdk_app_id_label": "",
        "hint": "尚未接入,配置后不能发送。",
        "help_url": "https://docs.aws.amazon.com/sns/latest/dg/sns-mobile-phone-number-as-subscriber.html",
    },
    {
        "key": "custom_http",
        "provider": "custom_http",
        "label": "自定义 HTTP 网关",
        "content_mode": "freeform",
        "region_scope": "all",
        "needs_sign_name": False,
        "needs_sdk_app_id": False,
        "needs_from_number": False,
        "key_id_label": "接口账号",
        "secret_label": "接口密钥",
        "sdk_app_id_label": "",
        "hint": "尚未接入,配置后不能发送。",
        "help_url": "",
    },
]


def find_sms_provider_preset(provider: str) -> dict[str, Any] | None:
    wanted = str(provider or "").strip().lower()
    if not wanted:
        return None
    return next((p for p in SALES_AGENT_SMS_PROVIDER_PRESETS if p["provider"] == wanted), None)


# Encryption, scope normalization and account selection are in channel_common; only SMS-specific wording remains here.
def _encrypt_sms_secret(secret: str) -> str:
    return encrypt_channel_secret(secret)


def _decrypt_sms_secret(encrypted: str) -> str:
    return decrypt_channel_secret(encrypted, "短信通道密钥无法解密,请到短信管理页重新保存 SecretKey / Auth Token。")


def _normalize_sms_scope(scope: str) -> str:
    return normalize_channel_scope(scope, "短信配置")


def _sms_scope_user_id(scope: str, user_id: int | None) -> int | None:
    return channel_scope_user_id(scope, user_id)


SMS_SENDER_SELECTION_POLICIES = CHANNEL_SENDER_SELECTION_POLICIES


def normalize_sms_sender_selection_policy(value: str) -> str:
    return normalize_channel_sender_policy(value, "短信发送调度策略")


def _normalize_sms_provider(value: str) -> str:
    provider = str(value or "tencent_cloud").strip().lower()
    if find_sms_provider_preset(provider) is None:
        allowed = "、".join(p["provider"] for p in SALES_AGENT_SMS_PROVIDER_PRESETS)
        raise SalesAgentError(f"短信平台只能是 {allowed} 之一。")
    return provider


def _normalize_content_mode(value: str, provider: str) -> str:
    mode = str(value or "").strip().lower()
    if not mode:
        preset = find_sms_provider_preset(provider)
        return str(preset["content_mode"]) if preset else "template"
    if mode not in SMS_CONTENT_MODES:
        raise SalesAgentError("短信内容形态只能是 template(报备模板)或 freeform(自由文案)。")
    return mode


def _normalize_region_scope(value: str, provider: str) -> str:
    region = str(value or "").strip().lower()
    if not region:
        preset = find_sms_provider_preset(provider)
        return str(preset["region_scope"]) if preset else "cn"
    if region not in SMS_REGION_SCOPES:
        raise SalesAgentError("短信通道可发范围只能是 cn(国内)、intl(国际)或 all(不限)。")
    return region


def _normalize_msisdn_field(value: str) -> str:
    """Caller number is only normalized for storage: strip spaces and dashes, add the E.164 `+`."""
    text = "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+").strip()
    if not text:
        return ""
    if not text.startswith("+"):
        text = "+" + text
    if not text[1:].isdigit() or not (7 <= len(text[1:]) <= 15):
        raise SalesAgentError("主叫号码请填 E.164 格式(国家码 + 号码,如 +15551234567)。")
    return text[:32]


def _clamp_non_negative_int(value: Any, label: str, maximum: int) -> int:
    try:
        number = int(value or 0)
    except Exception as e:  # noqa: BLE001
        raise SalesAgentError(f"{label}必须是数字。") from e
    if number < 0:
        raise SalesAgentError(f"{label}不能是负数;填 0 表示不限。")
    return min(number, maximum)


def region_scope_matches_country(region_scope: str, country: str) -> bool:
    """Whether the channel's sendable range covers this country code. No restriction when `country` is empty (country not parsed from the number)."""
    scope = str(region_scope or "").strip().lower()
    code = str(country or "").strip().upper()
    if scope == "all" or not code:
        return True
    return code == "CN" if scope == "cn" else code != "CN"


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def _get_sms_setting_row(db: Session, scope: str, user_id: int | None, create: bool = False) -> SalesAgentSmsSettingORM | None:
    scope = _normalize_sms_scope(scope)
    uid = _sms_scope_user_id(scope, user_id)
    q = db.query(SalesAgentSmsSettingORM).filter(SalesAgentSmsSettingORM.scope == scope)
    q = q.filter(SalesAgentSmsSettingORM.user_id.is_(None)) if uid is None else q.filter(SalesAgentSmsSettingORM.user_id == uid)
    row = q.first()
    if row is None and create:
        row = SalesAgentSmsSettingORM(scope=scope, user_id=uid, sending_enabled=True, receipt_enabled=True)
        db.add(row)
        db.flush()
    return row


def serialize_sales_agent_sms_setting(row: SalesAgentSmsSettingORM | None, scope: str = "user", user_id: int | None = None) -> dict[str, Any]:
    if row is None:
        normalized = _normalize_sms_scope(scope)
        return {
            "id": None,
            "scope": normalized,
            "user_id": _sms_scope_user_id(normalized, user_id),
            "sending_enabled": True,
            "receipt_enabled": True,
            "default_account_id": None,
            "sender_selection_policy": "default_only",
            "daily_send_cap": 0,
            "per_contact_cooldown_days": 0,
            "updated_by": "",
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "scope": row.scope,
        "user_id": row.user_id,
        "sending_enabled": bool(row.sending_enabled),
        "receipt_enabled": bool(row.receipt_enabled),
        "default_account_id": row.default_account_id,
        "sender_selection_policy": normalize_sms_sender_selection_policy(getattr(row, "sender_selection_policy", "default_only")),
        "daily_send_cap": int(row.daily_send_cap or 0),
        "per_contact_cooldown_days": int(row.per_contact_cooldown_days or 0),
        "updated_by": row.updated_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------


def _sms_account_scope_query(db: Session, scope: str, user_id: int | None):
    return channel_account_scope_query(db, SalesAgentSmsAccountORM, scope, user_id, "短信配置")


def _get_sms_account_for_scope(db: Session, account_id: int, scope: str, user_id: int | None) -> SalesAgentSmsAccountORM:
    row = _sms_account_scope_query(db, scope, user_id).filter(SalesAgentSmsAccountORM.id == int(account_id)).first()
    if row is None:
        raise SalesAgentError("短信通道不存在或不属于当前范围。", status_code=404)
    return row


SMS_CALLBACK_TOKEN_KIND = "sales-agent-sms-callback"


def _sms_callback_url(row: SalesAgentSmsAccountORM) -> str:
    base = _sales_agent_public_base_url().rstrip("/")
    token = channel_callback_token(SMS_CALLBACK_TOKEN_KIND, int(row.id or 0))
    return f"{base}/api/sales-agent/sms/callback/{row.provider}/{token}" if token else ""


def serialize_sales_agent_sms_account(row: SalesAgentSmsAccountORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    preset = find_sms_provider_preset(row.provider)
    return {
        "id": row.id,
        "scope": row.scope,
        "user_id": row.user_id,
        "display_name": row.display_name,
        "provider": row.provider,
        "provider_label": preset["label"] if preset else row.provider,
        "content_mode": row.content_mode,
        "region_scope": row.region_scope,
        "enabled": bool(row.enabled),
        "is_default": bool(row.is_default),
        "sign_name": row.sign_name,
        "sdk_app_id": row.sdk_app_id,
        "has_api_key_id": bool(row.api_key_id_encrypted),
        "has_api_secret": bool(row.api_secret_encrypted),
        "api_base_url": row.api_base_url,
        "from_number": row.from_number,
        "provider_config": _loads(row.provider_config_json, {}),
        # Receipt/inbound webhook URL the user pastes into the SMS console. Channels without receipt support get an empty string,
        # so the admin page does not configure a URL the platform will never call, waiting for a delivered that never comes.
        "callback_url": _sms_callback_url(row) if provider_supports_callback(row.provider) else "",
        "supports_callback": provider_supports_callback(row.provider),
        "last_tested_at": _iso(row.last_tested_at),
        "last_test_status": row.last_test_status,
        "last_test_error": row.last_test_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _validate_sms_account(row: SalesAgentSmsAccountORM) -> None:
    preset = find_sms_provider_preset(row.provider)
    label = preset["label"] if preset else row.provider
    if preset is not None:
        if preset["needs_sign_name"] and not row.sign_name:
            raise SalesAgentError(f"{label}必须填写已报备的短信签名,没有签名平台会直接拒发。")
        if preset["needs_sdk_app_id"] and not row.sdk_app_id:
            raise SalesAgentError(f"{label}必须填写 {preset['sdk_app_id_label']},在短信控制台的应用列表里可以找到。")
        if preset["needs_from_number"] and not row.from_number:
            raise SalesAgentError(f"{label}必须填写主叫号码(E.164,如 +15551234567)。")
    if row.content_mode == "template" and row.region_scope == "intl":
        raise SalesAgentError("模板通道是国内短信的形态,可发范围不能设为国际;国际号码请另建 freeform 通道。")
    if not row.api_key_id_encrypted or not row.api_secret_encrypted:
        key_id_label = preset["key_id_label"] if preset else "接口账号"
        secret_label = preset["secret_label"] if preset else "接口密钥"
        raise SalesAgentError(f"{label}需要同时填写 {key_id_label} 与 {secret_label}。")


def _apply_sms_account_payload(row: SalesAgentSmsAccountORM, data: dict[str, Any], partial: bool = False) -> None:
    def present(key: str) -> bool:
        return key in data and (data[key] is not None or not partial)

    if present("display_name"):
        row.display_name = str(data.get("display_name") or "").strip()[:128]
    if present("provider"):
        row.provider = _normalize_sms_provider(str(data.get("provider") or "tencent_cloud"))
    if present("content_mode") or present("provider"):
        row.content_mode = _normalize_content_mode(str(data.get("content_mode") or (row.content_mode if partial else "")), row.provider)
    if present("region_scope") or present("provider"):
        row.region_scope = _normalize_region_scope(str(data.get("region_scope") or (row.region_scope if partial else "")), row.provider)
    if present("enabled"):
        row.enabled = bool(data.get("enabled"))
    if present("is_default"):
        row.is_default = bool(data.get("is_default"))
    if present("sign_name"):
        row.sign_name = str(data.get("sign_name") or "").strip()[:128]
    if present("sdk_app_id"):
        row.sdk_app_id = str(data.get("sdk_app_id") or "").strip()[:64]
    if present("api_base_url"):
        row.api_base_url = str(data.get("api_base_url") or "").strip()[:512]
    if present("from_number"):
        row.from_number = _normalize_msisdn_field(str(data.get("from_number") or ""))
    if present("provider_config"):
        config = data.get("provider_config") if isinstance(data.get("provider_config"), dict) else {}
        row.provider_config_json = _dumps(sanitize_channel_provider_config(config))
    # Secrets are only overwritten when this request carries a non-empty value: the frontend echoes has_* booleans, not ciphertext,
    # so a PATCH that "only changes enabled status" must not clear stored secrets.
    key_id = str(data.get("api_key_id") or "").strip() if "api_key_id" in data else ""
    if key_id:
        row.api_key_id_encrypted = _encrypt_sms_secret(key_id)
    api_secret = str(data.get("api_secret") or "").strip() if "api_secret" in data else ""
    if api_secret:
        row.api_secret_encrypted = _encrypt_sms_secret(api_secret)
    if not row.display_name:
        preset = find_sms_provider_preset(row.provider)
        row.display_name = f"{preset['label']} {row.sign_name}".strip() if preset else (row.sign_name or "短信通道")
    _validate_sms_account(row)


def _mark_default_sms_account(db: Session, row: SalesAgentSmsAccountORM) -> None:
    if not row.is_default:
        return
    peers = _sms_account_scope_query(db, row.scope, row.user_id).filter(SalesAgentSmsAccountORM.id != row.id).all()
    for peer in peers:
        peer.is_default = False
    setting = _get_sms_setting_row(db, row.scope, row.user_id, create=True)
    if setting is not None:
        setting.default_account_id = row.id


def create_sales_agent_sms_account(db: Session, user_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    scope = _normalize_sms_scope(scope)
    uid = _sms_scope_user_id(scope, user_id)
    row = SalesAgentSmsAccountORM(scope=scope, user_id=uid)
    _apply_sms_account_payload(row, data, partial=False)
    db.add(row)
    db.flush()
    _mark_default_sms_account(db, row)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_sms_account(row) or {}


def update_sales_agent_sms_account(db: Session, user_id: int, account_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    row = _get_sms_account_for_scope(db, account_id, scope, user_id)
    _apply_sms_account_payload(row, data, partial=True)
    if not row.enabled and row.is_default:
        row.is_default = False
        setting = _get_sms_setting_row(db, row.scope, row.user_id, create=False)
        if setting is not None and setting.default_account_id == row.id:
            setting.default_account_id = None
    _mark_default_sms_account(db, row)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_sms_account(row) or {}


def disable_sales_agent_sms_account(db: Session, user_id: int, account_id: int, scope: str = "user") -> dict[str, Any]:
    row = _get_sms_account_for_scope(db, account_id, scope, user_id)
    row.enabled = False
    row.is_default = False
    setting = _get_sms_setting_row(db, row.scope, row.user_id, create=False)
    if setting is not None and setting.default_account_id == row.id:
        setting.default_account_id = None
    db.commit()
    return {"ok": True, "account": serialize_sales_agent_sms_account(row)}


def update_sales_agent_sms_settings(
    db: Session,
    user_id: int,
    data: dict[str, Any],
    scope: str = "user",
    actor: str = "",
) -> dict[str, Any]:
    scope = _normalize_sms_scope(scope)
    row = _get_sms_setting_row(db, scope, user_id, create=True)
    assert row is not None
    if "sending_enabled" in data:
        row.sending_enabled = bool(data.get("sending_enabled"))
    if "receipt_enabled" in data:
        row.receipt_enabled = bool(data.get("receipt_enabled"))
    if "sender_selection_policy" in data:
        row.sender_selection_policy = normalize_sms_sender_selection_policy(str(data.get("sender_selection_policy") or "default_only"))
    if "daily_send_cap" in data:
        row.daily_send_cap = _clamp_non_negative_int(data.get("daily_send_cap"), "单日发送上限", 100000)
    if "per_contact_cooldown_days" in data:
        row.per_contact_cooldown_days = _clamp_non_negative_int(data.get("per_contact_cooldown_days"), "同号码冷却天数", 365)
    if "default_account_id" in data:
        default_id = data.get("default_account_id")
        if default_id:
            account = _get_sms_account_for_scope(db, int(default_id), scope, user_id)
            account.is_default = True
            row.default_account_id = account.id
            _mark_default_sms_account(db, account)
        else:
            row.default_account_id = None
    row.updated_by = actor[:128]
    db.commit()
    return get_sales_agent_sms_settings(db, user_id, scope=scope)


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------


def _normalize_template_purpose(value: str) -> str:
    purpose = str(value or "outreach").strip().lower()
    if purpose not in SMS_TEMPLATE_PURPOSES:
        raise SalesAgentError("模板用途只能是 outreach(首次触达)、followup(跟进)或 test(自检)。")
    return purpose


def _normalize_template_approval(value: str) -> str:
    status = str(value or "pending").strip().lower()
    if status not in SMS_TEMPLATE_APPROVAL_STATUSES:
        raise SalesAgentError("模板审核状态只能是 pending、approved 或 rejected。")
    return status


def _normalize_param_slots(value: Any) -> list[dict[str, Any]]:
    """Slot definitions determine what is validated at send time; getting them wrong yields "parameter count mismatch" on the platform side.

    Only accepts `{index, label, max_len}`; index must increase consecutively from 1, since the platform's {1}{2} are positional parameters,
    and a gap means the template body and this definition do not match.
    """
    items = value if isinstance(value, list) else []
    slots: list[dict[str, Any]] = []
    for i, item in enumerate(items[:20], start=1):
        raw = item if isinstance(item, dict) else {}
        try:
            index = int(raw.get("index") or i)
        except Exception as e:  # noqa: BLE001
            raise SalesAgentError("模板变量的 index 必须是数字。") from e
        if index != i:
            raise SalesAgentError(f"模板变量 index 必须从 1 开始连续编号,第 {i} 个填的是 {index}。")
        try:
            max_len = int(raw.get("max_len") or 0)
        except Exception as e:  # noqa: BLE001
            raise SalesAgentError("模板变量的 max_len 必须是数字。") from e
        slots.append({
            "index": index,
            "label": str(raw.get("label") or f"变量{index}").strip()[:64],
            "max_len": max(0, min(max_len, 500)),
        })
    return slots


def serialize_sales_agent_sms_template(row: SalesAgentSmsTemplateORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "account_id": row.account_id,
        "template_code": row.template_code,
        "name": row.name,
        "body": row.body,
        "param_slots": _loads(row.param_slots_json, []),
        "purpose": row.purpose,
        "approved_status": row.approved_status,
        "enabled": bool(row.enabled),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _apply_sms_template_payload(row: SalesAgentSmsTemplateORM, data: dict[str, Any], partial: bool = False) -> None:
    def present(key: str) -> bool:
        return key in data and (data[key] is not None or not partial)

    if present("template_code"):
        row.template_code = str(data.get("template_code") or "").strip()[:64]
    if present("name"):
        row.name = str(data.get("name") or "").strip()[:128]
    if present("body"):
        row.body = str(data.get("body") or "").strip()[:2000]
    if present("param_slots"):
        row.param_slots_json = _dumps(_normalize_param_slots(data.get("param_slots")))
    if present("purpose"):
        row.purpose = _normalize_template_purpose(str(data.get("purpose") or "outreach"))
    if present("approved_status"):
        row.approved_status = _normalize_template_approval(str(data.get("approved_status") or "pending"))
    if present("enabled"):
        row.enabled = bool(data.get("enabled"))
    if not row.template_code:
        raise SalesAgentError("请填写平台下发的模板 ID(腾讯云 TemplateId / 阿里云 TemplateCode),没有它无法发送。")
    if not row.name:
        row.name = row.template_code


def list_sales_agent_sms_templates(db: Session, user_id: int, account_id: int, scope: str = "user") -> dict[str, Any]:
    account = _get_sms_account_for_scope(db, account_id, scope, user_id)
    rows = (
        db.query(SalesAgentSmsTemplateORM)
        .filter(SalesAgentSmsTemplateORM.account_id == account.id)
        .order_by(SalesAgentSmsTemplateORM.enabled.desc(), SalesAgentSmsTemplateORM.id.desc())
        .all()
    )
    return {
        "account": serialize_sales_agent_sms_account(account),
        "templates": [x for x in (serialize_sales_agent_sms_template(row) for row in rows) if x],
    }


def create_sales_agent_sms_template(db: Session, user_id: int, account_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    account = _get_sms_account_for_scope(db, account_id, scope, user_id)
    if account.content_mode != "template":
        raise SalesAgentError("这是自由文案通道(freeform),不需要也不能挂报备模板;模板只对国内模板通道有效。")
    row = SalesAgentSmsTemplateORM(account_id=account.id)
    _apply_sms_template_payload(row, data, partial=False)
    exists = (
        db.query(SalesAgentSmsTemplateORM)
        .filter(SalesAgentSmsTemplateORM.account_id == account.id, SalesAgentSmsTemplateORM.template_code == row.template_code)
        .first()
    )
    if exists is not None:
        raise SalesAgentError(f"该通道下已存在模板 ID {row.template_code},请直接编辑那一条。")
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_sms_template(row) or {}


def _get_sms_template_for_scope(db: Session, user_id: int, template_id: int, scope: str = "user") -> SalesAgentSmsTemplateORM:
    row = db.query(SalesAgentSmsTemplateORM).filter(SalesAgentSmsTemplateORM.id == int(template_id)).first()
    if row is None:
        raise SalesAgentError("短信模板不存在。", status_code=404)
    # Verify the template's channel is really managed by the current scope, otherwise we would modify someone else's template.
    _get_sms_account_for_scope(db, int(row.account_id), scope, user_id)
    return row


def update_sales_agent_sms_template(db: Session, user_id: int, template_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    row = _get_sms_template_for_scope(db, user_id, template_id, scope)
    _apply_sms_template_payload(row, data, partial=True)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_sms_template(row) or {}


def disable_sales_agent_sms_template(db: Session, user_id: int, template_id: int, scope: str = "user") -> dict[str, Any]:
    row = _get_sms_template_for_scope(db, user_id, template_id, scope)
    row.enabled = False
    db.commit()
    return {"ok": True, "template": serialize_sales_agent_sms_template(row)}


def usable_sms_template(db: Session, account: SalesAgentSmsAccountORM, purpose: str = "outreach") -> SalesAgentSmsTemplateORM | None:
    """Templates under this channel usable for this purpose: must be enabled and approved by the platform.

    Unapproved templates would only be rejected by the platform, so there is no "send first and see" concession here.
    """
    return (
        db.query(SalesAgentSmsTemplateORM)
        .filter(
            SalesAgentSmsTemplateORM.account_id == int(account.id),
            SalesAgentSmsTemplateORM.enabled.is_(True),
            SalesAgentSmsTemplateORM.approved_status == "approved",
            SalesAgentSmsTemplateORM.purpose == _normalize_template_purpose(purpose),
        )
        .order_by(SalesAgentSmsTemplateORM.id.desc())
        .first()
    )


# --------------------------------------------------------------------------
# Effective config and account selection
# --------------------------------------------------------------------------


def _sms_setting_default_account_id(setting: SalesAgentSmsSettingORM | None) -> int | None:
    return int(setting.default_account_id) if setting is not None and setting.default_account_id else None


def _sms_sender_policy_for_setting(setting: SalesAgentSmsSettingORM | None) -> str:
    return normalize_sms_sender_selection_policy(
        getattr(setting, "sender_selection_policy", "default_only") if setting is not None else "default_only"
    )


def sms_account_candidates(db: Session, scope: str, user_id: int | None, country: str = "") -> list[SalesAgentSmsAccountORM]:
    """Enabled channels under this scope that can send to this country code.

    Country-code routing lands here: sending a US number with a CN signature will fail, so rather than sending and getting rejected by the platform,
    exclude unreachable channels at the selection stage.
    """
    rows = (
        _sms_account_scope_query(db, scope, user_id)
        .filter(SalesAgentSmsAccountORM.enabled.is_(True))
        .order_by(SalesAgentSmsAccountORM.is_default.desc(), SalesAgentSmsAccountORM.id.asc())
        .all()
    )
    return [row for row in rows if region_scope_matches_country(row.region_scope, country)]


def _pick_sms_account(
    db: Session,
    scope: str,
    user_id: int | None,
    setting: SalesAgentSmsSettingORM | None,
    country: str = "",
) -> SalesAgentSmsAccountORM | None:
    candidates = sms_account_candidates(db, scope, user_id, country)
    if not candidates:
        return None
    return pick_channel_sender_account(
        db,
        SalesAgentSmsAccountORM,
        SalesAgentSmsDeliveryORM,
        SalesAgentSmsDeliveryORM.sms_account_id,
        scope,
        user_id,
        policy=_sms_sender_policy_for_setting(setting),
        default_account_id=_sms_setting_default_account_id(setting),
        candidates=candidates,
    )


def _effective_sms_config(db: Session, user_id: int, country: str = "") -> dict[str, Any]:
    global_setting = _get_sms_setting_row(db, "global", None, create=False)
    user_setting = _get_sms_setting_row(db, "user", user_id, create=False)
    disabled_reasons: list[str] = []
    if global_setting is not None and not global_setting.sending_enabled:
        disabled_reasons.append("管理员已关闭 Sales Agent 短信发送")
    if user_setting is not None and not user_setting.sending_enabled:
        disabled_reasons.append("当前账号已关闭 Sales Agent 短信发送")
    account = _pick_sms_account(db, "user", user_id, user_setting, country)
    account_source = "user" if account is not None else "global"
    setting_in_effect = user_setting
    if account is None:
        account = _pick_sms_account(db, "global", None, global_setting, country)
        setting_in_effect = global_setting
    if account is None:
        # Say clearly whether "none is configured" or "configured but none can reach this country", otherwise users will keep re-checking their keys.
        if sms_account_candidates(db, "user", user_id) or sms_account_candidates(db, "global", None):
            where = f"国家码 {str(country).upper()} 的号码" if country else "该号码"
            disabled_reasons.append(f"已配置的短信通道都不支持发送到{where},请新建对应可发范围的通道")
        else:
            disabled_reasons.append("未配置可用短信通道")
    receipt_enabled = (global_setting.receipt_enabled if global_setting is not None else True) and (
        user_setting.receipt_enabled if user_setting is not None else True
    )
    return {
        "global_setting": global_setting,
        "user_setting": user_setting,
        "setting_in_effect": setting_in_effect,
        "account": account,
        "account_source": account_source if account is not None else "",
        "sender_selection_policy": _sms_sender_policy_for_setting(setting_in_effect),
        "sending_enabled": not disabled_reasons,
        "receipt_enabled": bool(receipt_enabled),
        "daily_send_cap": int(getattr(setting_in_effect, "daily_send_cap", 0) or 0),
        "per_contact_cooldown_days": int(getattr(setting_in_effect, "per_contact_cooldown_days", 0) or 0),
        "disabled_reasons": disabled_reasons,
    }


def resolve_effective_sms_settings(db: Session, user_id: int, country: str = "") -> dict[str, Any]:
    cfg = _effective_sms_config(db, user_id, country)
    return {
        "sending_enabled": bool(cfg["sending_enabled"]),
        "receipt_enabled": bool(cfg["receipt_enabled"]),
        "disabled_reasons": cfg["disabled_reasons"],
        "account_source": cfg["account_source"],
        "sender_selection_policy": cfg.get("sender_selection_policy", "default_only"),
        "daily_send_cap": cfg["daily_send_cap"],
        "per_contact_cooldown_days": cfg["per_contact_cooldown_days"],
        "account": serialize_sales_agent_sms_account(cfg["account"]),
        "global_setting": serialize_sales_agent_sms_setting(cfg["global_setting"], "global"),
        "user_setting": serialize_sales_agent_sms_setting(cfg["user_setting"], "user", user_id),
    }


def get_sales_agent_sms_settings(db: Session, user_id: int, scope: str = "user") -> dict[str, Any]:
    scope = _normalize_sms_scope(scope)
    row = _get_sms_setting_row(db, scope, user_id, create=False)
    return {
        "settings": serialize_sales_agent_sms_setting(row, scope, user_id),
        "effective": resolve_effective_sms_settings(db, user_id),
        "provider_options": SALES_AGENT_SMS_PROVIDER_OPTIONS,
        "provider_presets": SALES_AGENT_SMS_PROVIDER_PRESETS,
    }


def list_sales_agent_sms_accounts(
    db: Session,
    user_id: int,
    scope: str = "user",
    admin: bool = False,
    target_user_id: int | None = None,
) -> dict[str, Any]:
    scope_text = str(scope or "user").strip().lower()
    effective_user_id = int(target_user_id or user_id)
    if admin and scope_text == "all":
        rows = (
            db.query(SalesAgentSmsAccountORM)
            .order_by(SalesAgentSmsAccountORM.scope.asc(), SalesAgentSmsAccountORM.is_default.desc(), SalesAgentSmsAccountORM.id.desc())
            .all()
        )
        setting_payload = None
    else:
        scope_text = _normalize_sms_scope(scope_text)
        rows = (
            _sms_account_scope_query(db, scope_text, effective_user_id)
            .order_by(SalesAgentSmsAccountORM.is_default.desc(), SalesAgentSmsAccountORM.id.desc())
            .all()
        )
        setting_payload = serialize_sales_agent_sms_setting(
            _get_sms_setting_row(db, scope_text, effective_user_id, create=False), scope_text, effective_user_id
        )
    return {
        "accounts": [x for x in (serialize_sales_agent_sms_account(row) for row in rows) if x],
        "settings": setting_payload,
        "effective": resolve_effective_sms_settings(db, effective_user_id),
        "provider_options": SALES_AGENT_SMS_PROVIDER_OPTIONS,
        "provider_presets": SALES_AGENT_SMS_PROVIDER_PRESETS,
    }
