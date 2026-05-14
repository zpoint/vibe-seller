"""
Event sync abstraction layer.

Backends register via @register_backend decorator. The EventSyncer
dispatches sync operations to the appropriate backend.
"""

from abc import ABC, abstractmethod
import json
import logging

from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

BACKENDS_CONFIG_PATH = VIBE_SELLER_DIR / 'config' / 'event_backends.json'


class EventBackend(ABC):
    """Base class for event sync backends (Dida365, Google Calendar, etc.)."""

    @abstractmethod
    async def create_event(
        self,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> str:
        """Create an event in the external backend. Returns external ID."""
        ...

    @abstractmethod
    async def update_event(
        self,
        external_id: str,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> None:
        """Update an existing event in the external backend."""
        ...

    @abstractmethod
    async def delete_event(self, external_id: str) -> None:
        """Delete an event from the external backend."""
        ...


EVENT_BACKEND_REGISTRY: dict[str, type[EventBackend]] = {}


def register_backend(name: str):
    """Decorator to register an event backend."""

    def wrapper(cls: type[EventBackend]) -> type[EventBackend]:
        EVENT_BACKEND_REGISTRY[name] = cls
        return cls

    return wrapper


def load_backend_config(backend_name: str) -> dict:
    """Load backend configuration from ~/.vibe-seller/config/event_backends.json."""
    if not BACKENDS_CONFIG_PATH.exists():
        return {}
    try:
        all_config = json.loads(
            BACKENDS_CONFIG_PATH.read_text(encoding='utf-8')
        )
        return all_config.get(backend_name, {})
    except Exception:
        return {}


def save_backend_config(backend_name: str, config: dict) -> None:
    """Save backend configuration."""
    BACKENDS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    all_config = {}
    if BACKENDS_CONFIG_PATH.exists():
        try:
            all_config = json.loads(
                BACKENDS_CONFIG_PATH.read_text(encoding='utf-8')
            )
        except Exception:
            pass

    all_config[backend_name] = config
    BACKENDS_CONFIG_PATH.write_text(
        json.dumps(all_config, indent=2), encoding='utf-8'
    )


class EventSyncer:
    """Syncs events to external backends."""

    async def sync_event(
        self,
        backend_name: str,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> str:
        """Sync an event to the specified backend. Returns external ID."""
        backend_cls = EVENT_BACKEND_REGISTRY.get(backend_name)
        if not backend_cls:
            raise ValueError(
                f'Unknown backend: {backend_name}. Available: {list(EVENT_BACKEND_REGISTRY.keys())}'
            )

        config = load_backend_config(backend_name)
        backend = backend_cls()
        if hasattr(backend, 'configure'):
            backend.configure(config)

        return await backend.create_event(
            title, description, event_date, deadline
        )

    async def update_event(
        self,
        backend_name: str,
        external_id: str,
        title: str,
        description: str | None,
        event_date: str | None,
        deadline: str | None,
    ) -> None:
        """Update an event in the external backend."""
        backend_cls = EVENT_BACKEND_REGISTRY.get(backend_name)
        if not backend_cls:
            raise ValueError(f'Unknown backend: {backend_name}')

        config = load_backend_config(backend_name)
        backend = backend_cls()
        if hasattr(backend, 'configure'):
            backend.configure(config)

        await backend.update_event(
            external_id, title, description, event_date, deadline
        )

    async def delete_event(self, backend_name: str, external_id: str) -> None:
        """Delete an event from the external backend."""
        backend_cls = EVENT_BACKEND_REGISTRY.get(backend_name)
        if not backend_cls:
            raise ValueError(f'Unknown backend: {backend_name}')

        config = load_backend_config(backend_name)
        backend = backend_cls()
        if hasattr(backend, 'configure'):
            backend.configure(config)

        await backend.delete_event(external_id)


# Singleton
event_syncer = EventSyncer()
