"""Benchmarks, tuning, evaluation and the review queue.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations


import argparse
import sys

from server.core.runtime_config import resolve_runtime_config


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run the recall-quality benchmark harness (hit@k / MRR)."""
    import asyncio

    from server.core.benchmark import run_benchmark

    mode = args.embedder_mode or resolve_runtime_config().embedder_mode
    metrics = asyncio.run(run_benchmark(embedder_mode=mode, top_k=args.top_k))

    print("\n  LEVH recall benchmark")
    print("  " + "=" * 38)
    for k, v in metrics.items():
        print(f"  {k:14} {v}")
    print("  " + "=" * 38)
    if metrics["embedder_mode"] == "hash":
        print("  Note: hash embedder is non-semantic — pass --embedder-mode "
              "local/openai for a real quality signal.")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Fit the H(x,ψ) weights to the labelled query set and report the gain.

    Offline analysis only — this prints recommended HSCORE_* values and never
    changes runtime behaviour.
    """
    import asyncio

    from server.core.tuning import print_report, run_tuning

    mode = args.embedder_mode or resolve_runtime_config().embedder_mode
    report = asyncio.run(
        run_tuning(
            embedder_mode=mode,
            top_k=args.top_k,
            iterations=args.iterations,
            seed=args.seed,
        )
    )
    print_report(report)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """List memories due for review, or apply a review action to one."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            if args.review_command == "list":
                items = await engine.review_queue(
                    threshold=args.threshold,
                    project=getattr(args, "project", None) or None,
                    limit=args.limit,
                )
                if not items:
                    print("  No memories due for review.")
                    return 0
                print(f"\n  {len(items)} memories due for review")
                print("  " + "=" * 44)
                for it in items:
                    print(f"  [{it['id'][:8]}] retention {it['retention']}  {it['content']}")
                    print(f"           {it['reason']}")
                print("  " + "=" * 44)
                print("  Apply: levh review apply <id> --action "
                      "keep|reinforce|weaken|forget|pin|snooze")
                return 0
            if args.review_command == "apply":
                try:
                    result = await engine.apply_review(
                        args.memory_id, args.action, snooze_days=args.snooze_days
                    )
                except ValueError as exc:
                    print(f"  {exc}", file=sys.stderr)
                    return 1
                if not result.get("ok"):
                    print(f"  No memory {args.memory_id}.", file=sys.stderr)
                    return 1
                print(f"  Applied '{args.action}' to {args.memory_id[:8]}: {result}")
                return 0
            print("  Usage: levh review list | review apply <id> --action ...",
                  file=sys.stderr)
            return 1
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


def cmd_eval_run(args: argparse.Namespace) -> int:
    """Run the golden-fixture memory evaluation and write the report."""
    import asyncio
    import json

    from server.core.evaluation import run_evaluation, seed_demo_completion

    async def _run() -> dict:
        report = await run_evaluation(
            fixture_dir=args.fixtures or None,
            embedder_mode=args.embedder_mode or "hash",
        )
        report["product"] = {
            "seed_demo": await seed_demo_completion(
                embedder_mode=args.embedder_mode or "hash"
            )
        }
        return report

    report = asyncio.run(_run())
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    passed = sum(1 for f in report["fixtures"] if f["passed"])
    print(f"\n  Memory evaluation ({report['evaluation_version']}, "
          f"levh {report['levh_version']}, embedder={report['embedder_mode']})")
    print(f"  fixtures: {passed}/{report['fixture_count']} passed")
    r = report["recall"]
    print(f"  recall:   hit@1 {r['hit_at_1']}  hit@3 {r['hit_at_3']}  MRR {r['mrr']}")
    c = report["conflicts"]
    print(f"  conflict: precision {c['precision']}  recall {c['recall']}  "
          f"false positives {c['false_positives']}")
    print(f"  report → {args.output}\n")
    return 0 if passed == report["fixture_count"] else 1


def cmd_eval_report(args: argparse.Namespace) -> int:
    """Print the last written evaluation report."""
    import json
    import os

    if not os.path.exists(args.output):
        print(f"No evaluation report at {args.output}. Run `levh eval run` first.")
        return 1
    with open(args.output, encoding="utf-8") as fh:
        print(json.dumps(json.load(fh), indent=2, ensure_ascii=False))
    return 0
