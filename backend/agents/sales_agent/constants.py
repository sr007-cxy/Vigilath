"""Sales Agent MVP constants and hard boundaries."""

LEAD_STATUSES = {
    "New", "Diagnosed", "Recommended", "Draft Ready", "Pending Approval", "Contacted",
    "Replied", "Interested", "Meeting Booked", "Paused", "Invalid", "Undone", "Unsubscribed", "Blacklisted",
}

LEAD_STATUS_REASONS = {
    "manual", "no_fit", "duplicate", "bad_contact", "risk", "unsubscribed", "blacklisted",
    "replied", "interested", "meeting", "followup", "other",
}

OUTREACH_CHANNELS = {"email", "sms", "phone_script", "contact_form", "linkedin_script"}

DISCOVERY_SOURCES = {
    "internal_seed", "manual", "user_provided", "public_company_site", "official_directory",
    "authorized_partner", "public_registry",
}

CONTACT_SOURCES = {
    "user_provided", "public_company_site", "official_contact_form", "official_directory",
    "authorized_partner", "public_registry", "manual", "internal_seed",
}

BLOCKED_CONTACT_CONSENT = {"unsubscribed", "blacklisted", "do_not_contact"}
CONTACT_CONSENT_STATUSES = {"unknown", "public", "business", "consented", *BLOCKED_CONTACT_CONSENT}
CONTACT_CONFIDENCE = {"high", "medium", "low"}

# Default options of the four find-customers filter cards. They're both card options and the extraction word list of `flow/slots.py` —
# both must share the source, otherwise an industry the user mentions isn't recognized while the card lists it.
TARGET_INDUSTRY_OPTIONS = (
    "不限行业", "工业制造", "企业服务", "软件SaaS", "电商零售", "教育培训", "医疗健康", "口腔医疗",
    "美容医美", "餐饮酒店", "本地生活", "房地产家居", "金融保险", "法律财税", "法律服务", "汽车服务",
    "文旅旅游",
)

TARGET_REGION_OPTIONS = (
    "不限地区", "一线城市", "北京", "上海", "深圳", "广州", "杭州", "苏州", "南京", "成都",
    "武汉", "西安", "重庆", "长三角", "珠三角", "京津冀", "华东", "华南",
)

DEFAULT_RECOMMENDATIONS = 100
MAX_RECOMMENDATIONS = 100
DEFAULT_PROFILE_REGIONS = ["全国"]
DEFAULT_BUYER_ROLES = ["市场负责人", "品牌负责人", "增长负责人", "创始人/CEO"]
DEFAULT_EXCLUDE_RULES = ["已退订", "黑名单", "来源不明联系人", "私人联系方式", "合规风险联系人"]

# Max number of recipient addresses to try per company. Only **recipient-side** failures roll over to the next; sender-side failures don't
# (scheduled sending design 4.1.1). Externally it's still "1 company = 1 successful email", not mass-sending to multiple people at once.
MAX_RECIPIENT_FALLBACK = 2

RISK_LEVELS = {"low", "medium", "high"}
COMPLIANCE_DECISIONS = {"allowed", "blocked", "needs_approval"}

REPORT_TABLE_ALIASES = {
    "full": "full_diagnosis_reports",
    "full_diagnosis": "full_diagnosis_reports",
    "full_diagnosis_reports": "full_diagnosis_reports",
    "detection": "detection_records",
    "detection_record": "detection_records",
    "detection_records": "detection_records",
}
