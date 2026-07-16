"""Connector Registry — Maps connector names to their classes.

Usage:
    from server.connectors import CONNECTOR_REGISTRY, get_connector
    connector = get_connector("local_files")
    await connector.connect(config)
    memories = await connector.fetch()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseConnector

# Lazy-imported registry — populated on first access
_REGISTRY: dict[str, type[BaseConnector]] | None = None


def _ensure_registry() -> dict[str, type[BaseConnector]]:
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from .local_files import LocalFilesConnector
    from .obsidian import ObsidianConnector
    from .notion import NotionConnector
    from .github import GitHubConnector
    from .calendar import CalendarConnector
    from .email_connector import EmailConnector
    from .transcript import TranscriptConnector

    _REGISTRY = {
        LocalFilesConnector.name: LocalFilesConnector,
        ObsidianConnector.name: ObsidianConnector,
        NotionConnector.name: NotionConnector,
        GitHubConnector.name: GitHubConnector,
        CalendarConnector.name: CalendarConnector,
        EmailConnector.name: EmailConnector,
        TranscriptConnector.name: TranscriptConnector,
    }
    return _REGISTRY


def get_connector(name: str) -> BaseConnector:
    """Instantiate a connector by name.

    Raises:
        KeyError: If the connector name is not found.
    """
    registry = _ensure_registry()
    cls = registry.get(name)
    if cls is None:
        available = ", ".join(sorted(registry.keys()))
        raise KeyError(
            f"Unknown connector '{name}'. Available: {available}"
        )
    return cls()


def list_connectors() -> list[dict]:
    """Return metadata for every registered connector.

    Returns a list of dicts with keys: ``name``, ``description``,
    ``required_config_keys``.
    """
    registry = _ensure_registry()
    result = []
    for name, cls in sorted(registry.items()):
        instance = cls()
        result.append({
            "name": instance.name,
            "description": instance.description,
            "required_config_keys": instance.required_config_keys(),
        })
    return result


def get_registry() -> dict[str, type["BaseConnector"]]:
    """Public accessor for the connector registry (name → class)."""
    return _ensure_registry()