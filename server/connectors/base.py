"""Base Connector — Abstract interface for all app connectors.

Every connector must implement ``connect``, ``fetch``, and ``disconnect``.
The ``fetch`` method should return a list of dicts that are compatible
with the Memory model (at minimum: ``content``, ``tags``, ``metadata``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    """Abstract base class for importing data from external apps."""

    name: str = "base"
    description: str = "Base connector interface"

    @abstractmethod
    async def connect(self, config: dict) -> bool:
        """Establish connection / validate credentials.

        Args:
            config: Connector-specific configuration (API keys, paths, etc.).

        Returns:
            ``True`` if connection succeeded.
        """

    @abstractmethod
    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch data from the external source.

        Returns:
            A list of memory-compatible dicts with at least:
            ``content`` (str), ``tags`` (list[str]), ``metadata`` (dict).
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Clean up resources (close sessions, file handles, etc.)."""

    def required_config_keys(self) -> list[str]:
        """Return the list of required config keys for this connector.

        Override in subclasses to declare what keys ``connect()`` needs.
        """
        return []

    def help_text(self) -> str:
        """Return human-readable help for this connector."""
        lines = [
            f"Connector: {self.name}",
            f"  {self.description}",
            f"  Required config keys: {', '.join(self.required_config_keys()) or 'none'}",
        ]
        return "\n".join(lines)