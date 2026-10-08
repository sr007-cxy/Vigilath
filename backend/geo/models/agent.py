"""Agent-related tables (conversational GEO assistant).

agent_materials — knowledge base of user-uploaded materials ("knowledge base = user info"), searched by ask_knowledge
+ grounding for later diagnosis/draft generation. The MVP uses keyword search, so no vector column yet (an embedding column can be added later for semantic search).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from geo.database import Base


class AgentMaterialORM(Base):
    __tablename__ = "agent_materials"
    __table_args__ = (Index("idx_agent_mat_acct_topic", "account_id", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)   # = user/account, tenant isolation
    topic_id = Column(Integer, nullable=True)                   # Optional associated topic
    source = Column(String(length=512), nullable=False, default="")   # url or file name
    title = Column(String(length=512), nullable=False, default="")
    text = Column(Text, nullable=False, default="")            # Parsed plain text
    embedding_json = Column(Text, nullable=True)               # Vector (JSON float array, DashScope); empty = fall back to keywords
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentConversationORM(Base):
    """Account-level multi-turn conversation memory (session unit = account × business line). messages_json stores the Pydantic AI-serialized message history.

    Why scopes: the system prompt is serialized into messages_json (a known gap in architecture-status §13),
    and existing sessions always use the copy persisted at the time. When ops use the same account, chatting first on the customer page and then entering the ops workbench,
    a shared session would make the model still see the customer-line old prompt containing "paid gate / only serve the current account",
    with the two-step confirmation rules nowhere in it — "everything looks normal, but the safety mechanism is not in effect".
    Unique on (account_id, scope), physically isolating the two lines' histories.
    """
    __tablename__ = "agent_conversations"
    __table_args__ = (
        UniqueConstraint("account_id", "scope", name="uq_agent_conv_acct_scope"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)
    scope = Column(String(length=16), nullable=False, default="self")     # self = customer self-service | admin = ops console
    messages_json = Column(Text, nullable=False, default="[]")            # Serialized by ModelMessagesTypeAdapter
    # Flow slots (agents/geo/flow/). Keeping them in a separate column from messages_json is deliberate:
    # message history is **for the model** and gets trimmed by time/count; slots are **for code** and must not be trimmed.
    # Mixing state like "industry term still missing" into message history means continuing to let the model remember state for us —
    # that is exactly how it was lost in the 2026-08-27 <brand-a> case. Structure: {"diagnosis": {...}, ...}, one key per flow.
    context_json = Column(Text, nullable=False, default="{}", server_default="{}")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentTokenORM(Base):
    """Account-level token for external access (see docs/03-系统设计/Agent/对外开放-契约.md §12).

    A 1-year token, API-key style, for external agents (Xiaolongxia) to connect to this GEO agent.
    The token itself is a signed JWT (the DB only stores tid + metadata, no plaintext); validation **must check enabled** (the revocation lifeline for long-lived tokens).
    """
    __tablename__ = "agent_tokens"

    id = Column(Integer, primary_key=True, index=True)
    tid = Column(String(length=64), nullable=False, unique=True, index=True)   # token id = the JWT's tid
    account_id = Column(Integer, nullable=False, index=True)                    # The account this Xiaolongxia represents
    caps = Column(String(length=128), nullable=False, default="read")          # Comma-separated: read[,write]
    # Call policy tier (call_policies.key). Empty = use the channel default tier channel:embed / channel:skill.
    # Configurable per partner: automation or API, run now or queue, daily count, allowed engines.
    policy_key = Column(String(length=64), nullable=False, default="")
    origins = Column(Text, nullable=False, default="")                         # Comma-separated CORS whitelist (only checked for direct browser connections)
    label = Column(String(length=255), nullable=False, default="")             # Note (issued to whom)
    enabled = Column(Integer, nullable=False, default=1)                       # 0 = revoked
    expires_at = Column(DateTime, nullable=False)                              # Default +1 year
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentIMConnectorORM(Base):
    """IM connector (self-built app mode). Customers create a self-built app in their own Feishu/WeCom and paste the app credentials here.

    One self-built app = one Vigilath account: callbacks look up this table by app_id → account_id,
    and all IM users under the app converse as this account (data isolation via account_id).
    """
    __tablename__ = "agent_im_connectors"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(length=16), nullable=False)        # feishu / wecom
    app_id = Column(String(length=128), nullable=False, index=True)   # Feishu App ID / WeCom CorpID
    app_secret = Column(String(length=256), nullable=False, default="")
    verify_token = Column(String(length=128), nullable=False, default="")   # Event subscription Verification Token / WeCom Token
    aes_key = Column(String(length=128), nullable=False, default="")        # Feishu Encrypt Key / WeCom EncodingAESKey (empty = no encryption)
    config_json = Column(Text, nullable=False, default="{}")    # Platform-specific (WeCom agent_id, etc.)
    last_chat_id = Column(String(length=256), nullable=False, default="")  # Session/user identifier of the latest conversation, for proactive push-back
    enabled = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentNotificationORM(Base):
    """Proactive notifications (high-risk sentiment, etc.). The web side pulls unread ones when the chat window opens; the IM side pushes back best-effort."""
    __tablename__ = "agent_notifications"
    __table_args__ = (Index("ix_agent_notif_acct", "account_id", "read_at"),)

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False)
    title = Column(String(length=255), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    dedup_key = Column(String(length=128), nullable=False, unique=True, index=True)  # Dedup: the same event is not pushed twice
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)


class AgentIMUserBindingORM(Base):
    """IM user ↔ Vigilath account binding (ISV mode, each user binds individually).

    One ISV app serves countless enterprises; a Feishu user is located by (platform, tenant_key, open_id) → account_id.
    """
    __tablename__ = "agent_im_user_bindings"
    __table_args__ = (Index("ix_agent_im_bind_lookup", "platform", "tenant_key", "open_id", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(length=16), nullable=False)        # feishu / wecom
    tenant_key = Column(String(length=128), nullable=False, default="")   # Feishu enterprise identifier (ISV)
    open_id = Column(String(length=128), nullable=False)        # The user's open_id under this app
    account_id = Column(Integer, nullable=False, index=True)     # Vigilath account bound to
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentIMBindCodeORM(Base):
    """One-time binding code: generated by the console for the logged-in account; the user sends it to the bot in IM to complete binding."""
    __tablename__ = "agent_im_bind_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(length=16), nullable=False, unique=True, index=True)
    account_id = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentISVStateORM(Base):
    """ISV dynamic state (mainly stores the app_ticket Feishu pushes periodically; required to get app_access_token)."""
    __tablename__ = "agent_isv_state"

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(String(length=128), nullable=False, unique=True, index=True)
    app_ticket = Column(String(length=256), nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentDeviceCodeORM(Base):
    """Device code authorization (simplified OAuth Device Authorization Grant).

    Lets the skill install command take **no parameters**: the CLI starts the flow, gets a device_code + a human-readable user_code,
    the terminal prompts the user to enter the code in a browser to approve, and the CLI polls until it gets a token.
    The token therefore never goes through the clipboard or shell history.

    ★ The DB never stores the plaintext token: approve only records account_id; the token is signed on the spot only when CLI polling hits approved
      (issue_token) and immediately marked consumed — one device_code can be exchanged for a token only once.
    """
    __tablename__ = "agent_device_codes"

    id = Column(Integer, primary_key=True, index=True)
    device_code = Column(String(length=64), nullable=False, unique=True, index=True)  # Secret for the CLI, never shown
    user_code = Column(String(length=16), nullable=False, unique=True, index=True)    # Short code for humans to read, XXXX-XXXX
    status = Column(String(length=16), nullable=False, default="pending")  # pending/approved/denied/consumed
    account_id = Column(Integer, nullable=True, index=True)                # Filled after approval
    client = Column(String(length=128), nullable=False, default="")        # Requester's self-description (hostname/agent name), shown on the approval page
    client_ip = Column(String(length=64), nullable=False, default="")      # Originating IP, shown on the approval page against phishing
    expires_at = Column(DateTime, nullable=False)
    last_poll_at = Column(DateTime, nullable=True)                         # Basis for poll rate limiting
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
