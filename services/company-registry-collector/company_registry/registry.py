"""Provider registry: name -> Provider instance.

To add a provider: implement a Provider subclass in providers/<name>.py and register one line in _BUILDERS.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .config import Settings
from .providers.base import Provider
from .providers.qichamao import QichamaoProvider

_BUILDERS: Dict[str, Callable[[Settings], Provider]] = {
    "qichamao": lambda s: QichamaoProvider(s),
    # Later:
    # "tianyancha": lambda s: TianyanchaProvider(s),
    # "qcc":        lambda s: QccProvider(s),
    # "aiqicha":    lambda s: AiqichaProvider(s),
}


def available_providers() -> List[str]:
    return sorted(_BUILDERS)


def get_provider(name: str, settings: Optional[Settings] = None) -> Provider:
    if name not in _BUILDERS:
        raise KeyError(f"未知 provider: {name}。可用: {', '.join(available_providers())}")
    return _BUILDERS[name](settings or Settings())
