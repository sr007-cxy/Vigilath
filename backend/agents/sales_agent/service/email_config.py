"""Sales Agent service layer: mailbox config: sender mailbox CRUD, secret encryption/decryption, scheduling policy and effective config."""
from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from agents.sales_agent.models import SalesAgentEmailAccountORM, SalesAgentEmailDeliveryORM, SalesAgentEmailSettingORM

from agents.sales_agent.service.common import SalesAgentError, _dumps, _is_email, _iso, _loads, _safe_email
from agents.sales_agent.service.channel_common import (
    CHANNEL_SENDER_SELECTION_POLICIES,
    channel_account_scope_query,
    channel_credential_fernet,
    channel_scope_user_id,
    decrypt_channel_secret,
    enabled_channel_accounts_for_scope,
    encrypt_channel_secret,
    normalize_channel_scope,
    normalize_channel_sender_policy,
    pick_channel_default_account,
    pick_channel_round_robin_account,
    sanitize_channel_provider_config,
)


SALES_AGENT_EMAIL_PROVIDER_OPTIONS = [
    {"provider_type": "smtp", "api_provider": "", "label": "SMTP 邮箱", "available": True},
    {"provider_type": "api", "api_provider": "resend", "label": "Resend API", "available": True},
    {"provider_type": "api", "api_provider": "sendgrid", "label": "SendGrid API", "available": False},
    {"provider_type": "api", "api_provider": "aliyun_dm", "label": "阿里云 DM", "available": False},
]
SALES_AGENT_EMAIL_STATUSES = {"disabled", "no_recipient", "sending", "sent", "failed"}

# Email provider presets: when the user picks 163 (or types a sender like xxx@163.com), host/port/encryption should fill themselves in.
#
# `domains` is for auto-detection: enterprise mail (NetEase Enterprise / Alibaba Enterprise / Feishu) domains belong to the customer and cannot be detected,
# so they must be picked manually and are left empty. `secret_label` is not copy pedantry: 163/QQ/Gmail want an authorization code / app password rather than
# the login password; getting this wrong is the most common "test send failed", so the label must follow the provider.
SALES_AGENT_EMAIL_PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "key": "netease_163",
        "label": "网易 163 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["163.com"],
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 994, "smtp_security": "ssl"}, {"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "客户端授权码",
        "hint": "登录网页版 163 邮箱 →「设置 → POP3/SMTP/IMAP」开启 SMTP 服务,把生成的客户端授权码填在下面;邮箱登录密码不能用于 SMTP。",
        "help_url": "https://help.mail.163.com/",
    },
    {
        "key": "netease_126",
        "label": "网易 126 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["126.com"],
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 994, "smtp_security": "ssl"}, {"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "客户端授权码",
        "hint": "登录网页版 126 邮箱 →「设置 → POP3/SMTP/IMAP」开启 SMTP 服务,把生成的客户端授权码填在下面;邮箱登录密码不能用于 SMTP。",
        "help_url": "https://help.mail.163.com/",
    },
    {
        "key": "netease_yeah",
        "label": "网易 yeah.net 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["yeah.net"],
        "smtp_host": "smtp.yeah.net",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 994, "smtp_security": "ssl"}, {"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "客户端授权码",
        "hint": "登录网页版 yeah.net 邮箱 →「设置 → POP3/SMTP/IMAP」开启 SMTP 服务,把生成的客户端授权码填在下面。",
        "help_url": "https://help.mail.163.com/",
    },
    {
        "key": "netease_qiye",
        "label": "网易企业邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": [],
        "smtp_host": "smtp.qiye.163.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 994, "smtp_security": "ssl"}],
        "secret_label": "邮箱密码或客户端授权码",
        "hint": "企业邮箱域名是贵司自己的,识别不出来,所以要手选。若管理员开启了「客户端专用密码」,这里填专用密码。",
        "help_url": "https://qiye.163.com/help/",
    },
    {
        "key": "qq_mail",
        "label": "QQ 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["qq.com", "foxmail.com", "vip.qq.com"],
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 587, "smtp_security": "starttls"}],
        "secret_label": "授权码",
        "hint": "登录网页版 QQ 邮箱 →「设置 → 账户」开启 SMTP 服务,生成 16 位授权码填在下面;QQ 密码不能用于 SMTP。",
        "help_url": "https://service.mail.qq.com/",
    },
    {
        "key": "tencent_exmail",
        "label": "腾讯企业邮箱 / 企业微信邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["exmail.qq.com"],
        "smtp_host": "smtp.exmail.qq.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 587, "smtp_security": "starttls"}],
        "secret_label": "邮箱密码或客户端专用密码",
        "hint": "用贵司自有域名的企业邮箱请手选这一项。开启了「安全登录」时要在邮箱设置里生成客户端专用密码。",
        "help_url": "https://exmail.qq.com/qy_mng_logic/doc",
    },
    {
        "key": "aliyun_mail",
        "label": "阿里云邮箱(个人版)",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["aliyun.com"],
        "smtp_host": "smtp.aliyun.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "邮箱密码",
        "hint": "需先在邮箱设置里开启 SMTP 服务。",
        "help_url": "https://help.aliyun.com/zh/mail/",
    },
    {
        "key": "aliyun_qiye",
        "label": "阿里企业邮箱(钉钉邮箱)",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["mxhichina.com"],
        "smtp_host": "smtp.qiye.aliyun.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "邮箱密码",
        "hint": "自有域名的阿里企业邮箱请手选这一项;部分老账号的服务器地址是 smtp.mxhichina.com,连不上时到高级设置里改。",
        "help_url": "https://help.aliyun.com/zh/mail/",
    },
    {
        "key": "feishu",
        "label": "飞书邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["feishu.cn"],
        "smtp_host": "smtp.feishu.cn",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 587, "smtp_security": "starttls"}],
        "secret_label": "邮箱密码或专用密码",
        "hint": "自有域名的飞书邮箱请手选这一项。需在飞书邮箱设置里开启 SMTP/IMAP 服务。",
        "help_url": "https://www.feishu.cn/hc/zh-CN/",
    },
    {
        "key": "gmail",
        "label": "Gmail",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["gmail.com", "googlemail.com"],
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 587, "smtp_security": "starttls"}],
        "secret_label": "应用专用密码",
        "hint": "必须先开启两步验证,再生成 16 位「应用专用密码」;Google 账号密码不能用于 SMTP。",
        "help_url": "https://support.google.com/mail/answer/185833",
    },
    {
        "key": "outlook",
        "label": "Outlook / Microsoft 365",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "alt_ports": [],
        "secret_label": "邮箱密码或应用密码",
        "hint": "微软已陆续停用 SMTP 基础认证:个人账号需生成应用密码,企业账号需管理员为该邮箱放开 SMTP AUTH。个人 outlook.com 若连不上,把服务器改成 smtp-mail.outlook.com。",
        "help_url": "https://support.microsoft.com/office/8361e398-8af4-4e97-b147-6c6c4ac95353",
    },
    {
        "key": "sina",
        "label": "新浪邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["sina.com", "sina.cn", "vip.sina.com"],
        "smtp_host": "smtp.sina.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "邮箱密码或授权码",
        "hint": "需先在邮箱设置里开启 SMTP 服务。",
        "help_url": "https://mail.sina.com.cn/",
    },
    {
        "key": "sohu",
        "label": "搜狐邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["sohu.com"],
        "smtp_host": "smtp.sohu.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "邮箱密码",
        "hint": "需先在邮箱设置里开启 SMTP 服务。",
        "help_url": "https://mail.sohu.com/",
    },
    {
        "key": "icloud",
        "label": "iCloud 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "alt_ports": [],
        "secret_label": "App 专用密码",
        "hint": "必须先开启双重认证,再生成 App 专用密码;Apple ID 密码不能用于 SMTP。",
        "help_url": "https://support.apple.com/102654",
    },
    {
        "key": "chinamobile_139",
        "label": "中国移动 139 邮箱",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": ["139.com"],
        "smtp_host": "smtp.139.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [{"smtp_port": 25, "smtp_security": "none"}],
        "secret_label": "邮箱密码或授权码",
        "hint": "需先在邮箱设置里开启 SMTP 服务。",
        "help_url": "https://mail.10086.cn/",
    },
    {
        "key": "custom_smtp",
        "label": "自定义 SMTP",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": [],
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "alt_ports": [],
        "secret_label": "邮箱密码或授权码",
        "hint": "自建邮件服务器或未列出的服务商:自己填服务器地址、端口和加密方式。",
        "help_url": "",
    },
    {
        "key": "resend_api",
        "label": "Resend API",
        "provider_type": "api",
        "api_provider": "resend",
        "domains": [],
        "smtp_host": "",
        "smtp_port": 0,
        "smtp_security": "",
        "alt_ports": [],
        "secret_label": "API Key",
        "hint": "走 HTTP API 发送,不用 SMTP。发件域名需先在 Resend 后台验证 DNS。",
        "help_url": "https://resend.com/docs/introduction",
    },
]

# Unlisted domains are guessed by the generic rule smtp.<domain>:465 SSL (Thunderbird does the same when it finds no ISPDB record).
# The hit rate is decent; a wrong guess just means the user edits one line in advanced settings, better than looking it up from scratch.
EMAIL_PRESET_GUESS_PORT = 465
EMAIL_PRESET_GUESS_SECURITY = "ssl"


# Encryption, scope normalization and account selection have moved down into channel_common (SMS uses the same).
# The original function names stay here as thin wrappers: error wording is mailbox-specific, and external callers and facade exports need no change.
_email_credential_fernet = channel_credential_fernet


def _encrypt_email_secret(secret: str) -> str:
    return encrypt_channel_secret(secret)


def _decrypt_email_secret(encrypted: str) -> str:
    return decrypt_channel_secret(encrypted, "邮箱密钥无法解密,请重新保存授权码或 API Key。")


def _scope_user_id(scope: str, user_id: int | None) -> int | None:
    return channel_scope_user_id(scope, user_id)


def _normalize_email_scope(scope: str) -> str:
    return normalize_channel_scope(scope, "邮箱配置")


EMAIL_SENDER_SELECTION_POLICIES = CHANNEL_SENDER_SELECTION_POLICIES


def normalize_sender_selection_policy(value: str) -> str:
    return normalize_channel_sender_policy(value, "发件调度策略")


def _normalize_provider_type(value: str) -> str:
    provider_type = str(value or "smtp").strip().lower()
    if provider_type not in {"smtp", "api"}:
        raise SalesAgentError("发件箱类型只能是 SMTP 或 API 服务商。")
    return provider_type


def _normalize_api_provider(value: str, provider_type: str) -> str:
    provider = str(value or "").strip().lower()
    if provider_type == "smtp":
        return ""
    if provider not in {"resend", "sendgrid", "aliyun_dm", "custom"}:
        raise SalesAgentError("API 服务商只能选择 resend、sendgrid、aliyun_dm 或 custom。")
    return provider


def _normalize_smtp_security(value: str) -> str:
    sec = str(value or "ssl").strip().lower()
    aliases = {"tls": "starttls", "start_tls": "starttls", "none": "none", "plain": "none", "ssl": "ssl"}
    sec = aliases.get(sec, sec)
    if sec not in {"ssl", "starttls", "none"}:
        raise SalesAgentError("SMTP 安全模式只能是 ssl、starttls 或 none。")
    return sec


def _email_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    domain = text.rsplit("@", 1)[-1] if "@" in text else text
    return domain.strip().strip(".")


def find_email_provider_preset(key: str) -> dict[str, Any] | None:
    wanted = str(key or "").strip().lower()
    if not wanted:
        return None
    return next((p for p in SALES_AGENT_EMAIL_PROVIDER_PRESETS if p["key"] == wanted), None)


def preset_for_smtp_host(host: str) -> dict[str, Any] | None:
    """Reverse-lookup the preset by SMTP server address: old sender mailboxes were saved before preset_key existed, so this fills in the label."""
    wanted = str(host or "").strip().lower()
    if not wanted:
        return None
    return next((p for p in SALES_AGENT_EMAIL_PROVIDER_PRESETS if p["smtp_host"] and p["smtp_host"].lower() == wanted), None)


def detect_email_provider_preset(email_or_domain: str) -> tuple[dict[str, Any] | None, str]:
    """Identify the provider by email domain.

    Returns `(preset, source)`: `preset` means a preset matched, `guess` means unlisted but inferred as smtp.<domain>,
    `""` means there is not even a domain. The guessed one is not in the catalog, so the frontend must ask the user to confirm.
    """
    domain = _email_domain(email_or_domain)
    if not domain or "." not in domain:
        return None, ""
    for preset in SALES_AGENT_EMAIL_PROVIDER_PRESETS:
        if domain in [str(d).lower() for d in preset.get("domains") or []]:
            return preset, "preset"
    return {
        "key": "custom_smtp",
        "label": f"{domain}(推测)",
        "provider_type": "smtp",
        "api_provider": "",
        "domains": [],
        "smtp_host": f"smtp.{domain}",
        "smtp_port": EMAIL_PRESET_GUESS_PORT,
        "smtp_security": EMAIL_PRESET_GUESS_SECURITY,
        "alt_ports": [],
        "secret_label": "邮箱密码或授权码",
        "hint": f"没有收录 {domain},已按通用规则推测为 smtp.{domain}:{EMAIL_PRESET_GUESS_PORT} SSL,请确认后再测试发送。",
        "help_url": "",
    }, "guess"


def _sanitize_provider_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return sanitize_channel_provider_config(config)


def _get_email_setting_row(db: Session, scope: str, user_id: int | None, create: bool = False) -> SalesAgentEmailSettingORM | None:
    scope = _normalize_email_scope(scope)
    uid = _scope_user_id(scope, user_id)
    q = db.query(SalesAgentEmailSettingORM).filter(SalesAgentEmailSettingORM.scope == scope)
    q = q.filter(SalesAgentEmailSettingORM.user_id.is_(None)) if uid is None else q.filter(SalesAgentEmailSettingORM.user_id == uid)
    row = q.first()
    if row is None and create:
        row = SalesAgentEmailSettingORM(scope=scope, user_id=uid, sending_enabled=True, tracking_enabled=True)
        db.add(row)
        db.flush()
    return row


def serialize_sales_agent_email_setting(row: SalesAgentEmailSettingORM | None, scope: str = "user", user_id: int | None = None) -> dict[str, Any]:
    if row is None:
        return {
            "id": None,
            "scope": _normalize_email_scope(scope),
            "user_id": _scope_user_id(_normalize_email_scope(scope), user_id),
            "sending_enabled": True,
            "tracking_enabled": True,
            "default_account_id": None,
            "sender_selection_policy": "default_only",
            "updated_by": "",
            "created_at": None,
            "updated_at": None,
        }
    return {
        "id": row.id,
        "scope": row.scope,
        "user_id": row.user_id,
        "sending_enabled": bool(row.sending_enabled),
        "tracking_enabled": bool(row.tracking_enabled),
        "default_account_id": row.default_account_id,
        "sender_selection_policy": normalize_sender_selection_policy(getattr(row, "sender_selection_policy", "default_only")),
        "updated_by": row.updated_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _account_preset(row: SalesAgentEmailAccountORM, provider_config: dict[str, Any]) -> dict[str, Any] | None:
    """Which provider the sender mailbox uses: prefer the stored preset_key; for old data reverse-lookup by SMTP address."""
    preset = find_email_provider_preset(str(provider_config.get("preset_key") or ""))
    if preset is not None:
        return preset
    if row.provider_type == "api":
        return next((p for p in SALES_AGENT_EMAIL_PROVIDER_PRESETS if p["provider_type"] == "api" and p["api_provider"] == row.api_provider), None)
    return preset_for_smtp_host(row.smtp_host)


def serialize_sales_agent_email_account(row: SalesAgentEmailAccountORM | None) -> dict[str, Any] | None:
    if row is None:
        return None
    provider_config = _loads(row.provider_config_json, {})
    preset = _account_preset(row, provider_config)
    return {
        "id": row.id,
        "scope": row.scope,
        "user_id": row.user_id,
        "display_name": row.display_name,
        "provider_type": row.provider_type,
        "api_provider": row.api_provider,
        "preset_key": preset["key"] if preset else "",
        "preset_label": preset["label"] if preset else "",
        "enabled": bool(row.enabled),
        "is_default": bool(row.is_default),
        "from_email": row.from_email,
        "from_name": row.from_name,
        "reply_to_email": row.reply_to_email,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_security": row.smtp_security,
        "smtp_username": row.smtp_username,
        "has_smtp_secret": bool(row.smtp_secret_encrypted),
        "has_api_key": bool(row.api_key_encrypted),
        "api_base_url": row.api_base_url,
        "provider_config": provider_config,
        "last_tested_at": _iso(row.last_tested_at),
        "last_test_status": row.last_test_status,
        "last_test_error": row.last_test_error,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _account_scope_query(db: Session, scope: str, user_id: int | None):
    return channel_account_scope_query(db, SalesAgentEmailAccountORM, scope, user_id, "邮箱配置")


def _get_email_account_for_scope(db: Session, account_id: int, scope: str, user_id: int | None) -> SalesAgentEmailAccountORM:
    row = _account_scope_query(db, scope, user_id).filter(SalesAgentEmailAccountORM.id == int(account_id)).first()
    if row is None:
        raise SalesAgentError("发件箱不存在或不属于当前范围。", status_code=404)
    return row


def _validate_email_account(row: SalesAgentEmailAccountORM) -> None:
    if not _is_email(row.from_email):
        raise SalesAgentError("请填写有效的发件邮箱。")
    if row.reply_to_email and not _is_email(row.reply_to_email):
        raise SalesAgentError("请填写有效的回复邮箱。")
    if row.provider_type == "smtp":
        if not row.smtp_host:
            raise SalesAgentError("SMTP 发件箱需要填写服务器地址。")
        if not (1 <= int(row.smtp_port or 0) <= 65535):
            raise SalesAgentError("SMTP 端口必须在 1-65535 之间。")
    elif row.provider_type == "api" and row.api_provider not in {"resend", "sendgrid", "aliyun_dm", "custom"}:
        raise SalesAgentError("API 服务商配置不正确。")


def apply_email_provider_preset(row: SalesAgentEmailAccountORM, preset_key: str = "") -> tuple[dict[str, Any] | None, str]:
    """Fill provider preset values into empty fields, returning `(preset, source)`.

    There is only one rule: **act only while `smtp_host` is still empty**. Port and encryption come as a set with host; keeping a user-entered
    port alone would produce combos like `smtp.163.com:587` that cannot send; anyone customizing the port has also filled in host, and then presets never trigger.
    """
    preset = find_email_provider_preset(preset_key)
    source = "preset" if preset is not None else ""
    if preset is None:
        preset, source = detect_email_provider_preset(row.from_email)
    if preset is None:
        return None, ""
    if preset["provider_type"] == "api" and row.provider_type == "api":
        if not row.api_provider:
            row.api_provider = preset["api_provider"]
        return preset, source
    if row.provider_type != "smtp":
        return preset, source
    if not row.smtp_host and preset["smtp_host"]:
        row.smtp_host = preset["smtp_host"]
        row.smtp_port = int(preset["smtp_port"] or 465)
        row.smtp_security = _normalize_smtp_security(preset["smtp_security"] or "ssl")
    if not row.smtp_username:
        # _send_smtp_message already falls back to from_email when sending; persisting early only makes it visible in the form
        row.smtp_username = row.from_email
    return preset, source


def _apply_email_account_payload(row: SalesAgentEmailAccountORM, data: dict[str, Any], partial: bool = False) -> None:
    def present(key: str) -> bool:
        return key in data and (data[key] is not None or not partial)

    if present("display_name"):
        row.display_name = str(data.get("display_name") or "").strip()[:128]
    if present("provider_type"):
        row.provider_type = _normalize_provider_type(str(data.get("provider_type") or "smtp"))
    if present("api_provider") or present("provider_type"):
        row.api_provider = _normalize_api_provider(str(data.get("api_provider") or row.api_provider or ""), row.provider_type)
    if present("enabled"):
        row.enabled = bool(data.get("enabled"))
    if present("is_default"):
        row.is_default = bool(data.get("is_default"))
    if present("from_email"):
        row.from_email = _safe_email(str(data.get("from_email") or ""))
    if present("from_name"):
        row.from_name = str(data.get("from_name") or "").strip()[:128]
    if present("reply_to_email"):
        row.reply_to_email = _safe_email(str(data.get("reply_to_email") or ""))
    if present("smtp_host"):
        row.smtp_host = str(data.get("smtp_host") or "").strip()[:255]
    if present("smtp_port"):
        try:
            row.smtp_port = int(data.get("smtp_port") or 465)
        except Exception as e:  # noqa: BLE001
            raise SalesAgentError("SMTP 端口必须是数字。") from e
    if present("smtp_security"):
        row.smtp_security = _normalize_smtp_security(str(data.get("smtp_security") or row.smtp_security or "ssl"))
    if present("smtp_username"):
        row.smtp_username = _safe_email(str(data.get("smtp_username") or ""))
    if present("api_base_url"):
        row.api_base_url = str(data.get("api_base_url") or "").strip()[:512]
    if present("provider_config"):
        row.provider_config_json = _dumps(_sanitize_provider_config(data.get("provider_config") if isinstance(data.get("provider_config"), dict) else {}))
    smtp_secret = str(data.get("smtp_secret") or "").strip() if "smtp_secret" in data else ""
    if smtp_secret:
        row.smtp_secret_encrypted = _encrypt_email_secret(smtp_secret)
    api_key = str(data.get("api_key") or "").strip() if "api_key" in data else ""
    if api_key:
        row.api_key_encrypted = _encrypt_email_secret(api_key)
    stored_config = _loads(row.provider_config_json, {})
    # If this request did not carry preset_key, keep the stored one: otherwise a PATCH that "only changes enabled" would let domain detection overwrite the
    # custom_smtp the user picked earlier with netease_163, changing the provider label in the list even though the user touched nothing.
    wanted_preset_key = str(data.get("preset_key") or stored_config.get("preset_key") or "")
    preset, preset_source = apply_email_provider_preset(row, wanted_preset_key)
    if preset is not None and preset_source == "preset":
        # Guessed presets are not recorded: recording them would show "guessed" as "user-selected" in the list
        stored_config["preset_key"] = preset["key"]
        row.provider_config_json = _dumps(_sanitize_provider_config(stored_config))
    if not row.display_name:
        row.display_name = row.from_email or ("SMTP 发件箱" if row.provider_type == "smtp" else f"{row.api_provider or 'API'} 发件箱")
    _validate_email_account(row)


def _mark_default_email_account(db: Session, row: SalesAgentEmailAccountORM) -> None:
    if not row.is_default:
        return
    peers = _account_scope_query(db, row.scope, row.user_id).filter(SalesAgentEmailAccountORM.id != row.id).all()
    for peer in peers:
        peer.is_default = False
    setting = _get_email_setting_row(db, row.scope, row.user_id, create=True)
    if setting is not None:
        setting.default_account_id = row.id


def update_sales_agent_email_settings(
    db: Session,
    user_id: int,
    data: dict[str, Any],
    scope: str = "user",
    actor: str = "",
) -> dict[str, Any]:
    scope = _normalize_email_scope(scope)
    row = _get_email_setting_row(db, scope, user_id, create=True)
    assert row is not None
    if "sending_enabled" in data:
        row.sending_enabled = bool(data.get("sending_enabled"))
    if "tracking_enabled" in data:
        row.tracking_enabled = bool(data.get("tracking_enabled"))
    if "sender_selection_policy" in data:
        row.sender_selection_policy = normalize_sender_selection_policy(str(data.get("sender_selection_policy") or "default_only"))
    if "default_account_id" in data:
        default_id = data.get("default_account_id")
        if default_id:
            account = _get_email_account_for_scope(db, int(default_id), scope, user_id)
            account.is_default = True
            row.default_account_id = account.id
            _mark_default_email_account(db, account)
        else:
            row.default_account_id = None
    row.updated_by = actor[:128]
    db.commit()
    return get_sales_agent_email_settings(db, user_id, scope=scope)


def create_sales_agent_email_account(db: Session, user_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    scope = _normalize_email_scope(scope)
    uid = _scope_user_id(scope, user_id)
    row = SalesAgentEmailAccountORM(scope=scope, user_id=uid)
    _apply_email_account_payload(row, data, partial=False)
    db.add(row)
    db.flush()
    _mark_default_email_account(db, row)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_email_account(row) or {}


def update_sales_agent_email_account(db: Session, user_id: int, account_id: int, data: dict[str, Any], scope: str = "user") -> dict[str, Any]:
    row = _get_email_account_for_scope(db, account_id, scope, user_id)
    _apply_email_account_payload(row, data, partial=True)
    if not row.enabled and row.is_default:
        row.is_default = False
        setting = _get_email_setting_row(db, row.scope, row.user_id, create=False)
        if setting is not None and setting.default_account_id == row.id:
            setting.default_account_id = None
    _mark_default_email_account(db, row)
    db.commit()
    db.refresh(row)
    return serialize_sales_agent_email_account(row) or {}


def disable_sales_agent_email_account(db: Session, user_id: int, account_id: int, scope: str = "user") -> dict[str, Any]:
    row = _get_email_account_for_scope(db, account_id, scope, user_id)
    row.enabled = False
    row.is_default = False
    setting = _get_email_setting_row(db, row.scope, row.user_id, create=False)
    if setting is not None and setting.default_account_id == row.id:
        setting.default_account_id = None
    db.commit()
    return {"ok": True, "account": serialize_sales_agent_email_account(row)}


def _enabled_email_accounts_for_scope(db: Session, scope: str, user_id: int | None) -> list[SalesAgentEmailAccountORM]:
    return enabled_channel_accounts_for_scope(db, SalesAgentEmailAccountORM, scope, user_id)


def _setting_default_account_id(setting: SalesAgentEmailSettingORM | None) -> int | None:
    return int(setting.default_account_id) if setting is not None and setting.default_account_id else None


def _pick_default_account(db: Session, scope: str, user_id: int | None, setting: SalesAgentEmailSettingORM | None) -> SalesAgentEmailAccountORM | None:
    return pick_channel_default_account(db, SalesAgentEmailAccountORM, scope, user_id, _setting_default_account_id(setting))


def _sender_policy_for_setting(setting: SalesAgentEmailSettingORM | None) -> str:
    return normalize_sender_selection_policy(getattr(setting, "sender_selection_policy", "default_only") if setting is not None else "default_only")


def _pick_round_robin_account(db: Session, scope: str, user_id: int | None, setting: SalesAgentEmailSettingORM | None) -> SalesAgentEmailAccountORM | None:
    return pick_channel_round_robin_account(
        db,
        SalesAgentEmailAccountORM,
        SalesAgentEmailDeliveryORM,
        SalesAgentEmailDeliveryORM.email_account_id,
        scope,
        user_id,
        default_account_id=_setting_default_account_id(setting),
    )


def _pick_sender_account(db: Session, scope: str, user_id: int | None, setting: SalesAgentEmailSettingORM | None) -> SalesAgentEmailAccountORM | None:
    policy = _sender_policy_for_setting(setting)
    if policy == "round_robin":
        return _pick_round_robin_account(db, scope, user_id, setting)
    return _pick_default_account(db, scope, user_id, setting)


def _effective_email_config(db: Session, user_id: int) -> dict[str, Any]:
    global_setting = _get_email_setting_row(db, "global", None, create=False)
    user_setting = _get_email_setting_row(db, "user", user_id, create=False)
    disabled_reasons: list[str] = []
    if global_setting is not None and not global_setting.sending_enabled:
        disabled_reasons.append("管理员已关闭 Sales Agent 邮件发送")
    if user_setting is not None and not user_setting.sending_enabled:
        disabled_reasons.append("当前账号已关闭 Sales Agent 邮件发送")
    account = _pick_sender_account(db, "user", user_id, user_setting)
    account_source = "user" if account is not None else "global"
    sender_selection_policy = _sender_policy_for_setting(user_setting)
    if account is None:
        account = _pick_sender_account(db, "global", None, global_setting)
        sender_selection_policy = _sender_policy_for_setting(global_setting)
    if account is None:
        disabled_reasons.append("未配置可用发件箱")
    tracking_enabled = (global_setting.tracking_enabled if global_setting is not None else True) and (user_setting.tracking_enabled if user_setting is not None else True)
    return {
        "global_setting": global_setting,
        "user_setting": user_setting,
        "account": account,
        "account_source": account_source if account is not None else "",
        "sender_selection_policy": sender_selection_policy,
        "sending_enabled": not disabled_reasons,
        "tracking_enabled": bool(tracking_enabled),
        "disabled_reasons": disabled_reasons,
    }


def sales_agent_sender_contact_email(db: Session, user_id: int) -> str:
    cfg = _effective_email_config(db, user_id)
    account: SalesAgentEmailAccountORM | None = cfg.get("account")
    if account is None:
        return ""
    return str(account.reply_to_email or account.from_email or "").strip()


def resolve_effective_email_settings(db: Session, user_id: int) -> dict[str, Any]:
    cfg = _effective_email_config(db, user_id)
    return {
        "sending_enabled": bool(cfg["sending_enabled"]),
        "tracking_enabled": bool(cfg["tracking_enabled"]),
        "disabled_reasons": cfg["disabled_reasons"],
        "account_source": cfg["account_source"],
        "sender_selection_policy": cfg.get("sender_selection_policy", "default_only"),
        "account": serialize_sales_agent_email_account(cfg["account"]),
        "global_setting": serialize_sales_agent_email_setting(cfg["global_setting"], "global"),
        "user_setting": serialize_sales_agent_email_setting(cfg["user_setting"], "user", user_id),
    }


def get_sales_agent_email_settings(db: Session, user_id: int, scope: str = "user") -> dict[str, Any]:
    scope = _normalize_email_scope(scope)
    row = _get_email_setting_row(db, scope, user_id, create=False)
    return {
        "settings": serialize_sales_agent_email_setting(row, scope, user_id),
        "effective": resolve_effective_email_settings(db, user_id),
        "provider_options": SALES_AGENT_EMAIL_PROVIDER_OPTIONS,
        "provider_presets": SALES_AGENT_EMAIL_PROVIDER_PRESETS,
    }


def list_sales_agent_email_accounts(
    db: Session,
    user_id: int,
    scope: str = "user",
    admin: bool = False,
    target_user_id: int | None = None,
) -> dict[str, Any]:
    scope_text = str(scope or "user").strip().lower()
    effective_user_id = int(target_user_id or user_id)
    if admin and scope_text == "all":
        rows = db.query(SalesAgentEmailAccountORM).order_by(SalesAgentEmailAccountORM.scope.asc(), SalesAgentEmailAccountORM.is_default.desc(), SalesAgentEmailAccountORM.id.desc()).all()
        setting_payload = None
    else:
        scope_text = _normalize_email_scope(scope_text)
        rows = _account_scope_query(db, scope_text, effective_user_id).order_by(SalesAgentEmailAccountORM.is_default.desc(), SalesAgentEmailAccountORM.id.desc()).all()
        setting_payload = serialize_sales_agent_email_setting(_get_email_setting_row(db, scope_text, effective_user_id, create=False), scope_text, effective_user_id)
    return {
        "accounts": [x for x in (serialize_sales_agent_email_account(row) for row in rows) if x],
        "settings": setting_payload,
        "effective": resolve_effective_email_settings(db, effective_user_id),
        "provider_options": SALES_AGENT_EMAIL_PROVIDER_OPTIONS,
        "provider_presets": SALES_AGENT_EMAIL_PROVIDER_PRESETS,
    }
