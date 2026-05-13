from geo.database import Base, engine
from geo.models.user import UserORM
from geo.models.membership import (
    AnonymousCheckUsageORM,  # noqa: F401 (register ORM so create_all builds it)
    ContactSubmissionORM,  # noqa: F401 (register ORM)
    MembershipORM,
    UserCheckUsageORM,  # noqa: F401
    UserMembershipORM,
)
from geo.models.payment import PaymentSessionORM  # noqa: F401 (register ORM)
from geo.models.detection import DetectionRecordORM  # noqa: F401 (register ORM)
from geo.models.sentiment import (  # noqa: F401 (register ORM so create_all builds it)
    SentimentAccountORM,
    SentimentKnowledgeORM,
    SentimentRunLogORM,
    SentimentPlatformORM,
)
from geo.models.ai_telemetry import (  # noqa: F401 (register ORM)
    AiTelemetryTopicORM, AiTelemetryRunORM, AiTelemetryResponseORM,
    AiTelemetryQueryHitORM, AiTelemetryCellInsightORM, AiTelemetryTopicBriefingORM,
)

# Create all tables
Base.metadata.create_all(bind=engine)

# Idempotent ALTER for existing DBs (SQLite + MySQL 都兼容这种 try/except 写法).
# create_all 只建新表,不改已存在表的列 —— 这里补差异列.
def _apply_telemetry_migrations() -> None:  # pragma: no cover
    from sqlalchemy import text
    additions = [
        ("ai_telemetry_topics", "target", "TEXT NOT NULL DEFAULT ''"),
        ("ai_telemetry_topics", "target_aliases_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("ai_telemetry_topics", "clusters_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("ai_telemetry_responses", "hit", "BOOLEAN NOT NULL DEFAULT 0"),
        ("ai_telemetry_responses", "hit_excerpt", "TEXT"),
        ("ai_telemetry_responses", "source_url", "TEXT"),
        ("ai_telemetry_responses", "competitors_json", "TEXT"),
        ("ai_telemetry_responses", "citation_domains_json", "TEXT"),
        ("ai_telemetry_responses", "answer_format", "TEXT"),
        ("ai_telemetry_responses", "mention_position", "TEXT"),
    ]
    with engine.begin() as conn:
        for table, col, ddl in additions:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception:  # noqa: BLE001
                # 列已存在 / 表还没建好都吞掉(MySQL 错码不同,SQLite OperationalError)
                pass

_apply_telemetry_migrations()