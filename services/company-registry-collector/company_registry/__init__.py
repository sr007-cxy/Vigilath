"""company-registry-collector - company business-registry data collection module (multi-provider).

Supplies company recommendations / ICP profile data for the Vigilath Sales Agent. Collects business-registry + contact + personnel
fields from company data providers (Qichamao / Tianyancha / Qichacha / Aiqicha ...), sliced by
"keyword x city x capital band", deduplicated and normalized, output as CSV/JSON or served to upstream via FastAPI.

Providers plug in as adapters: see the `providers/base.py` interface + `registry.py` registration.

⚠️ Compliance prerequisite: login/membership-walled providers (e.g. Qichamao) are a type that
`docs/08-独立模块/SalesAgent/03-系统设计/企业推荐数据来源库.md` §3.2 lists as out of scope for phase one;
enable only with an authorized account, see README §0.
"""

__version__ = "0.1.0"

from .collector import Collector, CollectResult
from .config import Settings
from .models import Company
from .registry import available_providers, get_provider

__all__ = [
    "Collector", "CollectResult", "Settings", "Company",
    "get_provider", "available_providers", "__version__",
]
