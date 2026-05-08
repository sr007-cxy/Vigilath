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

# Create all tables
Base.metadata.create_all(bind=engine)