"""Secret audits, redaction and hard deletes.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import sys



def cmd_audit_secrets(args: argparse.Namespace) -> int:
    """Scan stored memories for secrets (credentials, tokens)."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.audit_secrets()
        finally:
            await engine.shutdown()

    audit = asyncio.run(_run())
    if audit["flagged"] == 0:
        print(f"  Scanned {audit['scanned']} memories — no secrets found.")
        return 0
    print(f"  Scanned {audit['scanned']} memories — {audit['flagged']} flagged:")
    for item in audit["items"]:
        secret_types = ", ".join(item["secret_types"])
        print(f"  [{item['id'][:8]}] {secret_types} — {item['preview']}")
    print("  Use `levh redact-secrets --apply` to strip them.")
    return 0


def cmd_redact(args: argparse.Namespace) -> int:
    """Strip secrets from stored memories (dry-run by default)."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.redact_all_secrets(dry_run=not args.apply)
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    if not args.apply:
        if result["flagged"] == 0:
            print(f"  Scanned {result['scanned']} memories — nothing to redact.")
        else:
            print(
                f"  {result['flagged']} of {result['scanned']} memories WOULD be "
                f"redacted. Re-run with --apply to rewrite them."
            )
    else:
        print(
            f"  Redacted {result['redacted']} of {result['scanned']} memories "
            f"({result['flagged']} flagged)."
        )
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Hard-delete a memory and verify it's fully gone."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.purge_memory(args.memory_id)
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    if not result["existed"]:
        print(f"  No memory {args.memory_id}.", file=sys.stderr)
        return 1
    if result["purged"]:
        print(f"  Purged {args.memory_id[:8]} — hard-deleted, fully absent from all layers.")
    else:
        print(f"  Purged {args.memory_id[:8]}, but residue remains: {result['residue']}")
    return 0
