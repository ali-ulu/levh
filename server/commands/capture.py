"""Storing memories from the command line, and connector sync.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import os
import sys



def _detect_project() -> str | None:
    """Use the current git repo's directory name as the project, if any."""
    import subprocess
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if top.returncode == 0 and top.stdout.strip():
            return os.path.basename(top.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def cmd_capture(args: argparse.Namespace) -> int:
    """Store a memory directly from the command line."""
    import asyncio

    from server.core import engine_provider

    content = args.content.strip()
    if not content:
        print("  Nothing to capture: content is empty.", file=sys.stderr)
        return 1

    project = args.project or _detect_project()
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            result = await engine.admit_memory(
                content=content,
                importance=args.importance,
                tags=tags,
                project=project,
                source=args.source,
                pinned=args.pin,
                memory_type="episodic",
            )
            if not result["stored"]:
                decision = result["decision"]
                print(
                    f"  Not captured (admission: {decision['action']}): "
                    f"{', '.join(decision['reasons'])}",
                    file=sys.stderr,
                )
                return 2
            memory = result["memory"]
            print(f"  Captured memory {memory['id'][:8]}...")
            print(f"  Project: {project or 'none'} | Tags: {', '.join(tags) or 'none'}"
                  f" | Pinned: {memory['pinned']}")
            if result["decision"]["redacted"]:
                print("  Admission: secrets redacted before storage")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def cmd_admit(args: argparse.Namespace) -> int:
    """Store a memory through the admission gate (dedupe + secret redaction)."""
    import asyncio

    from server.core import engine_provider

    content = args.content.strip()
    if not content:
        print("  Nothing to admit: content is empty.", file=sys.stderr)
        return 1

    project = args.project or _detect_project()

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.admit_memory(
                content, source="cli", project=project, force=args.force
            )
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    decision = result["decision"]
    action = decision["action"]

    if result["stored"]:
        memory = result["memory"] or {}
        print(f"  Stored (action: {action}) memory {str(memory.get('id', ''))[:8]}...")
        if decision["redacted"]:
            print("  Secrets were stripped before storing.")
    else:
        print(f"  Not stored (action: {action}).")
        print(f"  Reasons: {', '.join(decision['reasons'])}")
        print("  Use --force to store it anyway.")
    return 0


def _scrub(text: str, secrets: "list[str]") -> str:
    """Remove supplied config values from text destined for the terminal.

    Connector config carries tokens and API keys, and an exception message is
    not under our control — an HTTP client happily puts the URL, query string
    and all, into the error it raises. Since the exact values are known here,
    stripping them is reliable in a way that pattern-matching a message is not.
    """
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text


def cmd_sync(args: argparse.Namespace) -> int:
    """Connector v2: gate-filtered incremental import, or show sync state."""
    import asyncio

    from server.core import engine_provider

    if args.status:
        async def _status() -> list[dict]:
            engine = engine_provider.get_engine()
            await engine.initialize()
            try:
                return await engine.list_sync_state()
            finally:
                await engine.shutdown()

        rows = asyncio.run(_status())
        if not rows:
            print("  No connector syncs recorded yet.")
            return 0
        for row in rows:
            project = row.get("project") or "(no project)"
            print(
                f"  {row['connector']} [{project}] — last synced {row['last_synced_at']}, "
                f"{row['total_stored']} stored over {row['runs']} runs"
            )
        return 0

    if not args.connector:
        print("  Usage: levh sync <connector> [--project P] [--config KEY=VALUE ...] [--no-gate]", file=sys.stderr)
        print("         levh sync --status", file=sys.stderr)
        return 1

    config: dict = {}
    for kv in args.config:
        if "=" not in kv:
            # Never echo the argument itself: connector config carries
            # tokens and API keys, and a mistyped separator
            # (--config token:ghp_...) would print the credential.
            print(
                "  Ignoring a malformed --config value (expected KEY=VALUE)",
                file=sys.stderr,
            )
            continue
        key, _, value = kv.partition("=")
        config[key] = value

    project = args.project or _detect_project()

    async def _run() -> dict:
        from server.connectors import get_connector

        conn = get_connector(args.connector)
        await conn.connect(config)
        try:
            items = await conn.fetch()
        except Exception:
            await conn.disconnect()
            raise
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            result = await engine.ingest_items(
                items,
                connector=args.connector,
                project=project,
                use_gate=not args.no_gate,
            )
        finally:
            await engine.shutdown()
        await conn.disconnect()
        return result

    supplied_secrets = list(config.values())

    try:
        result = asyncio.run(_run())
    except KeyError as e:
        print(f"  {_scrub(str(e), supplied_secrets)}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        print(f"  Connection failed: {_scrub(str(e), supplied_secrets)}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"  Sync failed: {_scrub(str(e), supplied_secrets)}", file=sys.stderr)
        return 1

    print(
        f"  Synced '{result['connector']}': fetched {result['fetched']}, "
        f"stored {result['stored']}"
    )
    print(
        f"  Duplicates: {result['duplicates']} | Redacted: {result['redacted']} | "
        f"Held for review: {result['held']} | Errors: {result['errors']}"
    )
    print(f"  Project: {project or 'none'} | Last synced: {result['last_synced_at']}")
    return 0
