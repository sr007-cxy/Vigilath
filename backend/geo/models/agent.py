"""Agent 相关数据表(对话式 GEO 助手)。

agent_materials —— 用户上传的资料知识库(「知识库 = 用户信息」),供 ask_knowledge 检索
+ 后续诊断/产稿 grounding。MVP 用关键词检索,故先不带向量列(后续可加 embedding 列升级语义检索)。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base


class AgentMaterialORM(Base):
    __tablename__ = "agent_materials"
    __table_args__ = (Index("idx_agent_mat_acct_topic", "account_id", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)   # = 用户/账号,租户隔离
    topic_id = Column(Integer, nullable=True)                   # 可选关联主题
    source = Column(String(length=512), nullable=False, default="")   # url 或文件名
    title = Column(String(length=512), nullable=False, default="")
    text = Column(Text, nullable=False, default="")            # 解析后的纯文本
    embedding_json = Column(Text, nullable=True)               # 向量(JSON float 数组,DashScope);空=回退关键词
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentConversationORM(Base):
    """账号级多轮对话记忆(会话单位=账号↔助手)。messages_json 存 Pydantic AI 序列化的消息历史。"""
    __tablename__ = "agent_conversations"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, unique=True, index=True)  # 一账号一会话
    messages_json = Column(Text, nullable=False, default="[]")            # ModelMessagesTypeAdapter 序列化
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentTokenORM(Base):
    """对外开放的账号级 token(见 docs/对外开放设计-Agent小龙虾 §12)。

    1 年期 token,API-key 风格,给外部 agent(小龙虾)链接本 GEO agent 用。
    token 本身是签名 JWT(库里只存 tid + 元数据,不存明文);校验时**必查 enabled**(长期 token 吊销命门)。
    """
    __tablename__ = "agent_tokens"

    id = Column(Integer, primary_key=True, index=True)
    tid = Column(String(length=64), nullable=False, unique=True, index=True)   # token id = JWT 的 tid
    account_id = Column(Integer, nullable=False, index=True)                    # 这只小龙虾代表的账号
    caps = Column(String(length=128), nullable=False, default="read")          # 逗号分隔:read[,write]
    origins = Column(Text, nullable=False, default="")                         # 逗号分隔 CORS 白名单(浏览器直连才校验)
    label = Column(String(length=255), nullable=False, default="")             # 备注(发给谁)
    enabled = Column(Integer, nullable=False, default=1)                       # 0=吊销
    expires_at = Column(DateTime, nullable=False)                              # 默认 +1 年
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
