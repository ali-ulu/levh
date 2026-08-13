"""Entities, trust and conflict candidates.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import sys



def cmd_entities(args: argparse.Namespace) -> int:
    """Reindex / list / inspect entities in the persistent knowledge graph."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            if args.entities_command == "reindex":
                result = await engine.reindex_entities()
                by_type = result.get("by_type", {})
                breakdown = ", ".join(f"{k}: {v}" for k, v in by_type.items()) if by_type else "none"
                print(
                    f"  Indexed {result['memories']} memories -> {result['entities']} entities, "
                    f"{result['links']} links ({breakdown})"
                )
                return 0
            if args.entities_command == "list":
                entities = await engine.list_entities_graph(
                    entity_type=args.type or None, limit=args.limit
                )
                if not entities:
                    print("  No entities. Run `levh entities reindex` first.")
                    return 0
                print(f"\n  {len(entities)} entities")
                print("  " + "=" * 44)
                for e in entities:
                    print(f"  [{e['type']}] {e['name']} — {e['mentions']} mentions")
                print("  " + "=" * 44)
                return 0
            if args.entities_command == "about":
                result = await engine.get_entity(args.query)
                if result is None:
                    print(f"  No entity matching '{args.query}'.", file=sys.stderr)
                    return 1
                e = result["entity"]
                related = result["related"]
                memories = result["memories"]
                print(f"\n  [{e['type']}] {e['name']} — {e.get('mentions', 0)} mentions")
                if related:
                    print("\n  Related entities:")
                    for r in related[:8]:
                        print(f"    - [{r['type']}] {r['name']} (shared: {r['shared']})")
                if memories:
                    print("\n  Memories:")
                    for m in memories[:8]:
                        snippet = (m.get("content") or "").split("\n", 1)[0][:90]
                        when = (m.get("created_at") or "")[:10]
                        print(f"    - [{when}] {snippet}")
                    if len(memories) > 8:
                        print(f"    … and {len(memories) - 8} more")
                return 0
            print(
                "  Usage: levh entities reindex | entities list [--type T] "
                "[--limit N] | entities about <query>",
                file=sys.stderr,
            )
            return 1
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def cmd_trust(args: argparse.Namespace) -> int:
    """Show / recompute the provenance-trust score for memories. Deterministic,
    explainable, NOT truth — independent of H-score / recall ranking."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            if args.trust_command == "show":
                result = await engine.get_trust(args.memory_id)
                if result is None:
                    print(f"  No memory {args.memory_id}.", file=sys.stderr)
                    return 1
                c = result["components"]
                print(
                    f"\n  Trust for {result['memory_id'][:8]}: "
                    f"confidence {result['confidence']} ({result['label']})"
                )
                print("  " + "=" * 44)
                print(f"  source_score:         {c['source_score']}")
                print(f"  corroboration_score:  {c['corroboration_score']}")
                print(f"  review_score:         {c['review_score']}")
                print(f"  recency_score:        {c['recency_score']}")
                print(f"  risk_penalty:         {c['risk_penalty']}")
                print("  " + "=" * 44)
                print("  Explanation:")
                for line in result.get("explanation", []):
                    print(f"    - {line}")
                return 0
            if args.trust_command == "recompute":
                result = await engine.recompute_trust_scores()
                by_label = result.get("by_label", {})
                breakdown = ", ".join(f"{k}: {v}" for k, v in by_label.items()) if by_label else "none"
                print(f"  Scored {result['scored']} memories ({breakdown})")
                return 0
            if args.trust_command == "low":
                items = await engine.list_low_trust(threshold=args.threshold, limit=args.limit)
                if not items:
                    print("  No low-trust memories. Run `levh trust recompute` first.")
                    return 0
                print(f"\n  {len(items)} low-trust memories (threshold {args.threshold})")
                print("  " + "=" * 44)
                for it in items:
                    print(f"  [{it['label']}] {it['confidence']} — {it['memory_id'][:8]}")
                print("  " + "=" * 44)
                return 0
            print(
                "  Usage: levh trust show <id> | trust recompute | "
                "trust low [--threshold T] [--limit N]",
                file=sys.stderr,
            )
            return 1
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def cmd_conflicts(args: argparse.Namespace) -> int:
    """Detect / list / review conflict CANDIDATES — pairs of memories that
    share an entity and show an opposing surface pattern. Deterministic,
    offline, never a verdict, never auto-deletes a memory."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            if args.conflicts_command == "detect":
                result = await engine.detect_conflict_candidates()
                print(
                    f"  Detected {result['new_candidates']} new conflict candidate(s) "
                    f"out of {result['pairs_examined']} pair(s) examined "
                    f"({result['open_total']} open total)."
                )
                return 0
            if args.conflicts_command == "list":
                items = await engine.list_conflict_candidates(status=args.status or None)
                if not items:
                    print("  No conflict candidates.")
                    return 0
                print(f"\n  {len(items)} conflict candidate(s)")
                print("  " + "=" * 44)
                for it in items:
                    expl = it.get("explanation") or {}
                    print(
                        f"  [{it['id']}] {it['signal_type']} ({expl.get('detail', '')}) — "
                        f"confidence {it['confidence']}, status {it['status']}"
                    )
                    print(f"      A: {expl.get('a_preview', '')}")
                    print(f"      B: {expl.get('b_preview', '')}")
                print("  " + "=" * 44)
                return 0
            if args.conflicts_command == "review":
                result = await engine.review_conflict_candidate(args.conflict_id, args.action)
                if not result.get("ok"):
                    print(f"  No conflict {args.conflict_id}.", file=sys.stderr)
                    return 1
                conflict = result.get("conflict", {})
                print(
                    f"  Applied '{args.action}' to conflict {args.conflict_id} — "
                    f"status is now '{conflict.get('status')}'."
                )
                return 0
            print(
                "  Usage: levh conflicts detect | conflicts list [--status S] | "
                "conflicts review <id> --action A",
                file=sys.stderr,
            )
            return 1
        finally:
            await engine.shutdown()

    return asyncio.run(_run())
