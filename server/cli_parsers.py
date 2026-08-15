"""Argument parsers for the ``levh`` CLI.

Split from cli.py so the parser definitions and the dispatch chain each fit on
a screen. They still belong together conceptually — a subcommand needs a
parser here *and* a branch in cli.main() — which is why build_parser returns
the subparsers the dispatch needs to print help for.
"""

from __future__ import annotations

import argparse

from server.entrypoint import levh_version

from server.tools.profiles import DEFAULT_PROFILE  # noqa: F401


def build_parser(prog: str) -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the full CLI parser.

    Returns the parser and the subparsers cli.main() needs for print_help().
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description="LEVH - Local-first memory layer for AI agents and humans",
    )
    # Derived, never a second literal: a hard-coded semver here is exactly the
    # drift #46 hit, and `python -m server.cli --version` reaches this path
    # while the installed console script goes through server/entrypoint.py.
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {levh_version()}"
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # doctor
    sub.add_parser("doctor", help="Check system health and dependencies")

    # first-run setup / onboarding
    setup_p = sub.add_parser("setup", help="First-run setup for demo or real data")
    setup_mode = setup_p.add_mutually_exclusive_group()
    setup_mode.add_argument("--demo", action="store_true", help="Load deterministic demo data")
    setup_mode.add_argument("--real", action="store_true", help="Prepare an empty real-data store")
    setup_p.add_argument("--status", action="store_true", help="Print computed onboarding readiness")
    setup_p.add_argument("--client", type=str, default="claude", help="MCP client (default: claude)")
    setup_p.add_argument(
        "--profile",
        type=str,
        default="work",
        help="MCP profile: minimal | work (default) | admin | full",
    )

    # init
    init_p = sub.add_parser("init", help="Create local config directory and defaults")
    init_p.add_argument("--force", action="store_true", help="Overwrite existing config")
    init_p.add_argument("--embedder-mode", type=str, help="Embedder mode (hash/local/openai)")
    init_p.add_argument("--db-path", type=str, help="Database file path")

    # serve
    serve_p = sub.add_parser("serve", help="Launch the API server")
    serve_p.add_argument("--host", type=str, default=None, help="Bind host override")
    serve_p.add_argument("--port", type=int, default=None, help="Bind port override")
    serve_p.add_argument("--reload", action="store_true", help="Enable auto-reload")

    # capture
    capture_p = sub.add_parser("capture", help="Store a memory from the command line")
    capture_p.add_argument("content", type=str, help="Memory content")
    capture_p.add_argument("--tags", type=str, default="", help="Comma-separated tags")
    capture_p.add_argument("--project", type=str, default="", help="Project name (default: git repo name)")
    capture_p.add_argument("--importance", type=float, default=0.5, help="Importance 0-1")
    capture_p.add_argument("--source", type=str, default="cli", help="Source label")
    capture_p.add_argument("--pin", action="store_true", help="Pin the memory (never decays)")

    # admit
    admit_p = sub.add_parser("admit", help="Store a memory through the admission gate (dedupe + secret redaction)")
    admit_p.add_argument("content", help="Memory text")
    admit_p.add_argument("--project", type=str, default="")
    admit_p.add_argument("--force", action="store_true", help="Store even if the gate would reject/hold it")

    # attach
    attach_p = sub.add_parser("attach", help="Attach a local file to a memory as evidence (reference + derived text, not blob)")
    attach_p.add_argument("memory_id", help="ID of the memory to attach the file to")
    attach_p.add_argument("path", help="Local path to the file")
    attach_p.add_argument("--derived-text", type=str, default="", help="OCR/transcript/caption text recall can search")
    attach_p.add_argument("--derived-by", type=str, default="manual", help="Who/what produced --derived-text (default: manual)")

    # sync (Connector Framework v2)
    sync_p = sub.add_parser("sync", help="Connector v2: gate-filtered incremental import")
    sync_p.add_argument("connector", nargs="?", help="Connector name (e.g. calendar, local_files)")
    sync_p.add_argument("--project", type=str, default="")
    sync_p.add_argument("--config", action="append", default=[], metavar="KEY=VALUE", help="Connector config (repeatable)")
    sync_p.add_argument("--no-gate", action="store_true", help="Skip the admission gate")
    sync_p.add_argument("--status", action="store_true", help="Show sync state instead of syncing")

    # context
    context_p = sub.add_parser("context", help="Generate CLAUDE.md / .cursorrules from memories")
    context_p.add_argument("--project", type=str, default="", help="Project filter (default: git repo name)")
    context_p.add_argument("--style", type=str, default="claude", choices=["claude", "cursor"], help="Output format")
    context_p.add_argument("--output", "-o", type=str, default="", help="Write to file instead of stdout")

    # summarize
    summarize_p = sub.add_parser("summarize", help="Distill a session into one summary memory")
    summarize_p.add_argument("session_id", type=str, help="Session ID to summarize")

    # benchmark
    benchmark_p = sub.add_parser("benchmark", help="Run the recall-quality benchmark (hit@k / MRR)")
    benchmark_p.add_argument("--embedder-mode", type=str, default="", help="Embedder mode (default: $EMBEDDER_MODE or hash)")
    benchmark_p.add_argument("--top-k", type=int, default=5, help="Top-k for recall during the benchmark")

    # tune
    tune_p = sub.add_parser("tune", help="Fit H(x,psi) weights to the labelled query set (offline)")
    tune_p.add_argument("--embedder-mode", type=str, default="", help="Embedder mode (default: $EMBEDDER_MODE or hash)")
    tune_p.add_argument("--top-k", type=int, default=5, help="Top-k for recall during tuning")
    tune_p.add_argument("--iterations", type=int, default=400, help="Search iterations (default: 400)")
    tune_p.add_argument("--seed", type=int, default=0, help="Random seed; fixed seed = reproducible result")

    # hook
    hook_p = sub.add_parser("hook", help="Auto-capture and session-start hooks")
    hook_sub = hook_p.add_subparsers(dest="hook_command", help="Hook subcommands")
    for verb, verb_help in (
        ("install", "Install a hook"),
        ("uninstall", "Remove a hook"),
    ):
        hook_verb = hook_sub.add_parser(verb, help=verb_help)
        hook_verb.add_argument(
            "--client",
            type=str,
            default="git",
            choices=["git", "claude-code"],
            help=(
                "git (default): capture every commit message. "
                "claude-code: start every session with the continuity brief"
            ),
        )
        hook_verb.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Sessions to summarize in the brief (claude-code only)",
        )

    # mcp
    mcp_p = sub.add_parser("mcp", help="MCP server commands")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_command", help="MCP subcommands")

    # mcp config
    config_p = mcp_sub.add_parser("config", help="Generate MCP client config JSON")
    config_p.add_argument(
        "platform",
        type=str,
        help=(
            "Client platform (claude, cursor, windsurf, claude_code, vscode, cline, "
            "jcode, omp, opencode, codex, hermes, generic)"
        ),
    )
    config_p.add_argument("--embedder-mode", type=str, help="Embedder mode override")
    config_p.add_argument("--db-path", type=str, help="Database path override")
    config_p.add_argument(
        "--profile",
        type=str,
        default="work",
        help="MCP tool profile: minimal | work (default) | admin | full",
    )

    # mcp init
    init_srv_p = mcp_sub.add_parser(
        "init", help="Scaffold a new MCP server project (optionally with LEVH memory)"
    )
    init_srv_p.add_argument("name", type=str, help="Project name (becomes the directory)")
    init_srv_p.add_argument(
        "--with-memory",
        action="store_true",
        help="Mount the LEVH memory tools in the generated server",
    )
    init_srv_p.add_argument(
        "--template", type=str, default="python", help="Project template: python"
    )
    init_srv_p.add_argument(
        "--profile",
        type=str,
        default="work",
        help="MCP tool profile for the mounted memory tools (with --with-memory)",
    )
    init_srv_p.add_argument(
        "--deploy",
        type=str,
        default="",
        help="Also write deploy config: fly | railway | render | docker",
    )
    init_srv_p.add_argument(
        "--directory", type=str, default=".", help="Where to create the project"
    )
    init_srv_p.add_argument(
        "--force", action="store_true", help="Write into a non-empty directory"
    )

    # mcp profiles
    mcp_sub.add_parser("profiles", help="List MCP tool profiles and their tool counts")

    # mcp stdio
    mcp_sub.add_parser("stdio", help="Launch MCP stdio server")

    # eval (2.25 golden-fixture memory evaluation)
    eval_p = sub.add_parser("eval", help="Golden-fixture memory evaluation (offline, deterministic)")
    eval_sub = eval_p.add_subparsers(dest="eval_command", help="Eval subcommands")
    er = eval_sub.add_parser("run", help="Run the evaluation and write the report")
    er.add_argument("--fixtures", type=str, default="", help="Fixture directory (default: tests/fixtures/evaluation)")
    er.add_argument("--embedder-mode", type=str, default="hash", help="Embedder mode (default: hash — deterministic)")
    er.add_argument("--output", "-o", type=str, default="eval_report.json", help="Report output path")
    ep = eval_sub.add_parser("report", help="Print the last written evaluation report")
    ep.add_argument("--output", "-o", type=str, default="eval_report.json", help="Report path to print")

    # dogfood (2.25 local usage journal — local-only, no telemetry)
    dog_p = sub.add_parser("dogfood", help="Local dogfood journal (local-only; export is explicit)")
    dog_sub = dog_p.add_subparsers(dest="dogfood_command", help="Dogfood subcommands")
    ds = dog_sub.add_parser("status", help="Aggregate stats from the local journal")
    ds.add_argument(
        "--journal",
        type=str,
        default="",
        help=(
            "Journal path (default: $DOGFOOD_JOURNAL_PATH, else next to "
            "$SQLITE_DB_PATH, else ./dogfood_events.jsonl)"
        ),
    )
    de = dog_sub.add_parser("export", help="Write the aggregate report to a file (explicit user action)")
    de.add_argument("--journal", type=str, default="", help="Journal path")
    de.add_argument("--output", "-o", type=str, default="report.json", help="Output path")

    # review (spaced-repetition)
    review_p = sub.add_parser("review", help="Spaced-repetition review of fading memories")
    review_sub = review_p.add_subparsers(dest="review_command", help="Review subcommands")
    rl = review_sub.add_parser("list", help="List memories due for review")
    rl.add_argument("--threshold", type=float, default=0.5)
    rl.add_argument("--limit", type=int, default=20)
    rl.add_argument("--project", type=str, default="")
    ra = review_sub.add_parser("apply", help="Apply a review action to a memory")
    ra.add_argument("memory_id")
    ra.add_argument(
        "--action",
        required=True,
        choices=["keep", "reinforce", "weaken", "forget", "pin", "snooze"],
    )
    ra.add_argument("--snooze-days", type=int, default=7, dest="snooze_days")

    # audit-secrets / redact-secrets / purge (hard-delete + redaction audit)
    audit_p = sub.add_parser("audit-secrets", help="Scan stored memories for secrets (credentials, tokens)")

    redact_p = sub.add_parser("redact-secrets", help="Strip secrets from stored memories")
    redact_p.add_argument("--apply", action="store_true", help="Actually rewrite (default is a dry-run preview)")

    purge_p = sub.add_parser("purge", help="Hard-delete a memory and verify it's fully gone")
    purge_p.add_argument("memory_id")

    # seed-demo (onboarding: populate an empty store with demo data)
    seed_p = sub.add_parser(
        "seed-demo",
        help="Load a deterministic demo corpus so a first run has data to explore",
    )
    seed_p.add_argument(
        "--force",
        action="store_true",
        help="Seed even if the store already has memories",
    )

    # remove-demo (onboarding: strip the demo corpus back out)
    sub.add_parser(
        "remove-demo",
        help="Remove demo-tagged memories, leaving real data untouched",
    )

    # continue (autonomous session continuity)
    continue_p = sub.add_parser("continue", help="Show context to resume work (session DNA)")
    continue_p.add_argument("task", nargs="?", default="", help="Task/query to find relevant context")
    continue_p.add_argument("--project", type=str, default="", help="Project filter (default: git repo name)")
    continue_p.add_argument("--limit", type=int, default=5, help="Max sessions to consider")
    continue_p.add_argument("--since", type=str, default="", help="Only consider sessions since ISO date (e.g. 2026-01-01)")
    continue_p.add_argument(
        "--if-any",
        action="store_true",
        help="Print nothing when there is no activity (used by the session hook)",
    )

    # export-full (memories + entity graph + trust + conflicts, one file)
    export_full_p = sub.add_parser(
        "export-full",
        help="Export memories, entity graph, trust scores, and conflicts to one file",
    )
    export_full_p.add_argument(
        "--format",
        choices=["json", "sqlite", "pdf"],
        default="json",
        help="Output format (default: json)",
    )
    export_full_p.add_argument("--out", help="Output file path (default: levh-full-export.<format>)")

    # entities (persistent entity knowledge graph)
    ent_p = sub.add_parser("entities", help="Persistent entity knowledge graph")
    ent_sub = ent_p.add_subparsers(dest="entities_command")
    ent_sub.add_parser("reindex", help="Rebuild the entity graph from all memories")
    el = ent_sub.add_parser("list", help="List entities")
    el.add_argument("--type", type=str, default="")
    el.add_argument("--limit", type=int, default=20)
    ea = ent_sub.add_parser("about", help="Show one entity's profile")
    ea.add_argument("query")

    # trust (provenance / trust score)
    trust_p = sub.add_parser("trust", help="Provenance / trust score for memories")
    trust_sub = trust_p.add_subparsers(dest="trust_command")
    ts = trust_sub.add_parser("show", help="Show a memory's trust breakdown")
    ts.add_argument("memory_id")
    trust_sub.add_parser("recompute", help="Recompute all trust scores")
    tl = trust_sub.add_parser("low", help="List low-trust memories")
    tl.add_argument("--threshold", type=float, default=0.4)
    tl.add_argument("--limit", type=int, default=20)

    # conflicts (deterministic conflict-candidate review)
    conf_p = sub.add_parser("conflicts", help="Deterministic conflict-candidate review")
    conf_sub = conf_p.add_subparsers(dest="conflicts_command")
    conf_sub.add_parser("detect", help="Detect conflict candidates")
    cl = conf_sub.add_parser("list", help="List conflict candidates")
    cl.add_argument("--status", default="open")
    cr = conf_sub.add_parser("review", help="Review a candidate")
    cr.add_argument("conflict_id")
    cr.add_argument(
        "--action",
        required=True,
        choices=[
            "dismiss",
            "confirm",
            "resolve_keep_a",
            "resolve_keep_b",
            "mark_both_valid",
            "human_review",
        ],
    )


    return parser, {
        "review_p": review_p,
        "ent_p": ent_p,
        "trust_p": trust_p,
        "conf_p": conf_p,
        "hook_p": hook_p,
        "mcp_p": mcp_p,
        "eval_p": eval_p,
        "dog_p": dog_p,
    }
