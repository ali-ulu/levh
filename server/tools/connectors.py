"""Tool: Connectors — Import data from external apps into StackMemory.

Registers three MCP tools:
    - import_from_app: Run a connector to pull data into memory.
    - list_connectors: List all available connectors.
    - get_connector_help: Get help / config requirements for a connector.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:

    @mcp.tool()
    async def import_from_app(
        connector: str,
        config: str = "{}",
        batch_size: int = 50,
        importance: float = 0.5,
    ) -> str:
        """Import data from an external app (local files, Obsidian, Notion, GitHub).

        Args:
            connector: Connector name — one of "local_files", "obsidian",
                       "notion", "github".
            config: JSON string of connector-specific configuration.
                    Examples:
                      local_files:  {"directory": "/path/to/project"}
                      obsidian:     {"vault_path": "/path/to/vault"}
                      notion:       {"api_key": "ntn_...", "database_ids": ["..."]}
                      github:       {"token": "ghp_...", "repos": ["owner/repo"]}
            batch_size: Number of memories to store per batch. Default 50.
            importance: Default importance for imported memories (0-1). Default 0.5.
        """
        import json

        from server.connectors import get_connector

        # Parse config
        try:
            config_dict = json.loads(config) if isinstance(config, str) else config
        except json.JSONDecodeError as e:
            return f"Invalid JSON in config: {e}"

        # Get connector
        try:
            conn = get_connector(connector)
        except KeyError as e:
            return str(e)

        # Connect
        try:
            await conn.connect(config_dict)
        except (FileNotFoundError, ValueError, ConnectionError) as e:
            return f"Connection failed: {e}"

        # Fetch
        try:
            items = await conn.fetch()
        except Exception as e:
            await conn.disconnect()
            return f"Fetch failed: {e}"

        # Legacy MCP import remains admission-gated; connector v2 adds
        # incremental sync state, but both surfaces share dedupe/redaction.
        try:
            result = await engine.ingest_items(
                items,
                connector=connector,
                project=None,
                use_gate=True,
            )
        finally:
            await conn.disconnect()

        return (
            f"Imported from '{connector}' through the admission gate.\n"
            f"  Connector: {conn.name}\n"
            f"  Items fetched: {result['fetched']}\n"
            f"  Stored: {result['stored']}\n"
            f"  Duplicates: {result['duplicates']}\n"
            f"  Redacted: {result['redacted']}\n"
            f"  Held: {result['held']}"
        )

    @mcp.tool()
    async def list_connectors() -> str:
        """List all available app connectors and their status.

        Returns connector names, descriptions, and required config keys.
        """
        from server.connectors import list_connectors as _list

        connectors = _list()
        if not connectors:
            return "No connectors available."

        lines = ["Available connectors:\n"]
        for c in connectors:
            keys = ", ".join(c["required_config_keys"]) or "none"
            lines.append(
                f"- {c['name']}\n"
                f"  {c['description']}\n"
                f"  Required config: {keys}"
            )
        return "\n\n".join(lines)

    @mcp.tool()
    async def get_connector_help(connector: str) -> str:
        """Get detailed help and configuration requirements for a connector.

        Args:
            connector: Connector name (e.g. "local_files", "obsidian", "notion", "github").
        """
        from server.connectors import get_connector

        try:
            conn = get_connector(connector)
        except KeyError as e:
            return str(e)

        return conn.help_text()