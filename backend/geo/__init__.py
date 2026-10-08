"""Package init — only registers ORM models on Base.metadata.

Schema creation/migration has moved to alembic (see backend/alembic/). Do NOT
call Base.metadata.create_all here: it races between uvicorn --workers N
spawn processes and bypasses the migration history alembic relies on.

New tables / columns: add the ORM model below, then run
    alembic revision --autogenerate -m "..."
in backend/, review the generated file, and commit.
"""
from geo.database import Base  # noqa: F401
from geo.models.user import UserORM  # noqa: F401
from geo.models.organization import (  # noqa: F401
    OperationContextORM,
    OrganizationGrantORM,
    OrganizationMemberAssignmentORM,
    OrganizationMemberORM,
    OrganizationORM,
    SystemAuditLogORM,
)
from geo.models.membership import (  # noqa: F401
    AnonymousCheckUsageORM,
    ContactSubmissionORM,
    MembershipORM,
    UserCheckUsageORM,
    UserMembershipORM,
)
from geo.models.payment import PaymentSessionORM  # noqa: F401
from geo.models.detection import DetectionRecordORM  # noqa: F401
from geo.models.full_diagnosis import FullDiagnosisReportORM  # noqa: F401
from geo.models.sentiment import (  # noqa: F401
    SentimentAccountORM,
    SentimentKnowledgeORM,
    SentimentRunLogORM,
    SentimentPlatformORM,
)
from geo.models.ai_telemetry import (  # noqa: F401
    AiTelemetryTopicORM,
    AiTelemetryRunORM,
    AiTelemetryResponseORM,
    AiTelemetryQueryHitORM,
    AiTelemetryCellInsightORM,
    AiTelemetryTopicBriefingORM,
)
from geo.models.blog import BlogPostORM  # noqa: F401
from geo.models.llm_usage import (  # noqa: F401
    LlmCallLogORM,
    LlmUsageDailyORM,
)
from geo.models.balance import (  # noqa: F401
    BalanceLedgerORM,
    BalancePriceORM,
    UserBalanceORM,
)
from geo.models.call_policy import (  # noqa: F401
    CallAuditORM,
    CallPolicyORM,
)
from geo.models.rwj_media import (  # noqa: F401
    RwjMediaResourceORM,
    RwjSyncRunORM,
)
from agents.sales_agent.models import (  # noqa: F401
    SalesAgentChatSessionORM,
    SalesAgentChatMessageORM,
    SalesAgentCustomerProfileORM,
    SalesAgentSellerProfileORM,
    SalesAgentAccountICPORM,
    SalesAgentBuyerPersonaORM,
    SalesAgentRecommendationCampaignORM,
    SalesAgentLeadEvidenceORM,
    SalesAgentCompanyORM,
    SalesAgentCompanyContactORM,
    SalesAgentLeadRecommendationORM,
    SalesAgentReportShareORM,
    SalesAgentLeadActivityORM,
    SalesAgentOutreachLogORM,
    SalesAgentEmailSettingORM,
    SalesAgentEmailAccountORM,
    SalesAgentEmailDeliveryORM,
    SalesAgentEmailEventORM,
    SalesAgentComplianceLogORM,
)
# Publish account pool (self-built server_pool channel). Note publish_accounts has a foreign key to
# engine_proxies, so the engine pool models must be registered together, otherwise Base.metadata would have
# only the referencing side and not the referenced one, and create_all / autogenerate would fail to resolve the target table.
from geo.models.engine_sessions import (  # noqa: F401
    EngineProxyORM,
    EngineSessionORM,
)
from geo.models.publish_accounts import (  # noqa: F401
    PublishAccountGroupORM,
    PublishAccountORM,
    PublishDispatchTaskORM,
    PublishPlatformORM,
)
from geo.models.aitoearn import (  # noqa: F401
    AitoearnAccountBindingORM,
    AitoearnAuthAttemptORM,
)
