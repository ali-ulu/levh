"""LEVH CLI — installer, doctor, init, MCP config generator, and server launcher.

Commands:
    levh doctor         Check system health and dependencies
    levh init           Create local config directory and defaults
    levh serve          Launch the API server (uvicorn)
    levh capture <txt>  Store a memory from the command line
    levh context        Generate CLAUDE.md / .cursorrules from memories
    levh hook install   Install a git post-commit hook that captures commits
    levh summarize <session_id>  Distill a session into one summary memory
    levh benchmark      Run the recall-quality benchmark (hit@k / MRR)
    levh tune           Fit H(x,psi) weights to the labelled set (offline)
    levh mcp config <platform>  Print MCP config JSON for a client
    levh mcp stdio      Launch MCP stdio server (for Claude Desktop etc.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Self-contained bootstrap: this has to run before importing anything under
# server.*, so it cannot come from server.commands.paths.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)



# ── Constants ─────────────────────────────────────────────────────




# ── doctor ────────────────────────────────────────────────────────



# ── setup / onboarding ───────────────────────────────────────────



# ── init ──────────────────────────────────────────────────────────



# ── serve ────────────────────────────────────────────────────────



# ── capture ──────────────────────────────────────────────────────







# ── sync (Connector Framework v2) ────────────────────────────────



# ── context (CLAUDE.md / .cursorrules generation) ────────────────



# ── hook install (git auto-capture) ──────────────────────────────








# ── summarize (session auto-capture) ─────────────────────────────



# ── benchmark (recall quality) ────────────────────────────────────



# ── tune (offline H-score weight fitting) ─────────────────────────



# ── review (spaced-repetition) ────────────────────────────────────



# ── entities (persistent entity knowledge graph) ─────────────────



# ── trust (provenance / trust score) ─────────────────────────────



# ── conflicts (deterministic conflict-candidate review) ──────────



# ── hard-delete + redaction audit ────────────────────────────────







# ── seed-demo (onboarding) ───────────────────────────────────────







# ── continue (autonomous session continuity) ────────────────────────



# ── mcp config ───────────────────────────────────────────────────



# ── mcp init ──────────────────────────────────────────────────────




# ── mcp profiles ──────────────────────────────────────────────────



# ── eval (2.25 memory evaluation) ─────────────────────────────────





# ── dogfood (2.25 local usage journal) ────────────────────────────





# ── mcp stdio ─────────────────────────────────────────────────────



# ── main ────────────────────────────────────────────────────────

# Command implementations live in server/commands/; the parsers and the
# dispatch chain below stay together so a new subcommand cannot land in one
# without the other.
from server.cli_parsers import build_parser
from server.commands.doctor import cmd_doctor
from server.commands.diagnostics import cmd_init, cmd_serve, cmd_setup
from server.commands.capture import cmd_admit, cmd_capture, cmd_sync
from server.commands.context import cmd_context, cmd_continue, cmd_summarize
from server.commands.hooks import cmd_hook
from server.commands.quality import cmd_benchmark, cmd_eval_report, cmd_eval_run, cmd_review, cmd_tune
from server.commands.knowledge import cmd_conflicts, cmd_entities, cmd_trust
from server.commands.privacy import cmd_audit_secrets, cmd_purge, cmd_redact
from server.commands.data import cmd_dogfood_export, cmd_dogfood_status, cmd_export_full, cmd_remove_demo, cmd_seed_demo
from server.commands.mcp import cmd_mcp_config, cmd_mcp_init, cmd_mcp_profiles, cmd_mcp_stdio


def main() -> int:
    invoked_as = Path(sys.argv[0]).stem.lower()
    legacy_invocation = invoked_as == "stackmemory"
    if legacy_invocation:
        print("'stackmemory' is deprecated; use 'levh'", file=sys.stderr)
    parser, groups = build_parser("stackmemory" if legacy_invocation else "levh")
    args = parser.parse_args()

    if args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "setup":
        return cmd_setup(args)
    elif args.command == "init":
        return cmd_init(args)
    elif args.command == "serve":
        return cmd_serve(args)
    elif args.command == "capture":
        return cmd_capture(args)
    elif args.command == "admit":
        return cmd_admit(args)
    elif args.command == "sync":
        return cmd_sync(args)
    elif args.command == "context":
        return cmd_context(args)
    elif args.command == "summarize":
        return cmd_summarize(args)
    elif args.command == "benchmark":
        return cmd_benchmark(args)
    elif args.command == "tune":
        return cmd_tune(args)
    elif args.command == "review":
        if args.review_command in ("list", "apply"):
            return cmd_review(args)
        groups["review_p"].print_help()
        return 1
    elif args.command == "audit-secrets":
        return cmd_audit_secrets(args)
    elif args.command == "redact-secrets":
        return cmd_redact(args)
    elif args.command == "purge":
        return cmd_purge(args)
    elif args.command == "seed-demo":
        return cmd_seed_demo(args)
    elif args.command == "remove-demo":
        return cmd_remove_demo(args)
    elif args.command == "continue":
        return cmd_continue(args)
    elif args.command == "export-full":
        return cmd_export_full(args)
    elif args.command == "entities":
        if args.entities_command in ("reindex", "list", "about"):
            return cmd_entities(args)
        groups["ent_p"].print_help()
        return 1
    elif args.command == "trust":
        if args.trust_command in ("show", "recompute", "low"):
            return cmd_trust(args)
        groups["trust_p"].print_help()
        return 1
    elif args.command == "conflicts":
        if args.conflicts_command in ("detect", "list", "review"):
            return cmd_conflicts(args)
        groups["conf_p"].print_help()
        return 1
    elif args.command == "hook":
        if args.hook_command in ("install", "uninstall"):
            return cmd_hook(args)
        groups["hook_p"].print_help()
        return 1
    elif args.command == "mcp":
        if args.mcp_command == "config":
            return cmd_mcp_config(args)
        elif args.mcp_command == "init":
            return cmd_mcp_init(args)
        elif args.mcp_command == "profiles":
            return cmd_mcp_profiles(args)
        elif args.mcp_command == "stdio":
            return cmd_mcp_stdio(args)
        else:
            groups["mcp_p"].print_help()
            return 1
    elif args.command == "eval":
        if args.eval_command == "run":
            return cmd_eval_run(args)
        elif args.eval_command == "report":
            return cmd_eval_report(args)
        groups["eval_p"].print_help()
        return 1
    elif args.command == "dogfood":
        if args.dogfood_command == "status":
            return cmd_dogfood_status(args)
        elif args.dogfood_command == "export":
            return cmd_dogfood_export(args)
        groups["dog_p"].print_help()
        return 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
