"""Tools: sync_connector / connector_sync_status — Connector Framework v2.
Fetch items from a connector and route them through the admission gate
(dedupe + secret redaction) with incremental sync bookkeeping, so re-syncing
the same source only stores what's new."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def sync_connector(
        connector: str,
        config_json: str = "",
        project: str = "",
        use_gate: bool = True,
    ) -> str:
        """Fetch items from a connector and store them via the admission
        gate (dedupe + secret redaction), recording sync bookkeeping so
        re-syncing the same source is incremental.

        Args:
            connector: Connector name (e.g. "local_files", "calendar").
            config_json: JSON object string of connector config (e.g.
                '{"directory": "/path"}'). Empty string means no config.
            project: Optional project to store memories under.
            use_gate: Route through the admission gate (dedupe + redact
                secrets). Default True. Set False to store everything as-is.
        """
        from server.connectors import get_connector

        if config_json.strip():
            try:
                config = json.loads(config_json)
            except json.JSONDecodeError as e:
                return f"Invalid config_json: {e}. Pass a JSON object, e.g. '{{\"directory\": \"/path\"}}'."
        else:
            config = {}

        try:
            conn = get_connector(connector)
        except KeyError as e:
            return str(e)

        try:
            await conn.connect(config)
        except (FileNotFoundError, ValueError, ConnectionError) as e:
            return f"Connection to '{connector}' failed: {e}"

        try:
            items = await conn.fetch()
        except Exception:
            await conn.disconnect()
            return f"Fetch from connector '{connector}' failed. See server logs."

        result = await engine.ingest_items(
            items, connector=connector, project=project or None, use_gate=use_gate
        )
        await conn.disconnect()

        return (
            f"Synced '{connector}': fetched {result['fetched']}, stored {result['stored']} "
            f"({result['duplicates']} duplicates, {result['redacted']} redacted, "
            f"{result['held']} held for review, {result['errors']} errors)."
        )

    @mcp.tool()
    async def connector_sync_status() -> str:
        """List connector sync bookkeeping: last sync time and totals per
        connector/project, most-recent first."""
        rows = await engine.list_sync_state()
        if not rows:
            return "No connector syncs recorded yet."
        lines = []
        for row in rows:
            project = row.get("project") or "(no project)"
            lines.append(
                f"{row['connector']} [{project}] — last synced {row['last_synced_at']}, "
                f"{row['total_stored']} stored over {row['runs']} runs"
            )
        return "\n".join(lines)
