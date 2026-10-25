"""Provider registry."""
from __future__ import annotations

import atexit
import logging
import threading

logger = logging.getLogger(__name__)

# One instance per provider class, shared by every caller for the life of the process.
_instances: dict[type, object] = {}
_instances_lock = threading.Lock()


def _get_providers():
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
    return {
        "taiwan": TaiwanHybridProvider,
    }


def get_provider(name: str = "taiwan"):
    """The shared instance of the provider registered under *name*; unknown names get "taiwan"."""
    providers = _get_providers()
    provider_cls = providers.get((name or "taiwan").lower(), providers["taiwan"])
    with _instances_lock:
        instance = _instances.get(provider_cls)
        if instance is None:
            instance = _instances[provider_cls] = provider_cls()
    return instance


def close_providers() -> None:
    """Close and forget the shared instances; the next get_provider builds new ones."""
    with _instances_lock:
        instances = list(_instances.values())
        _instances.clear()
    for instance in instances:
        close = getattr(instance, "close", None)
        if close is None:
            continue
        try:
            close()
        except Exception as exc:
            logger.warning("closing provider %s failed: %s", type(instance).__name__, exc)


atexit.register(close_providers)
