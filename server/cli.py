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

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is on the path
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


from server.core.runtime_config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULTS as RUNTIME_DEFAULTS,
    resolve_runtime_config,
    runtime_env,
)
from server.core.env import get_env

# ── Constants ─────────────────────────────────────────────────────

MCP_DIR = "mcp"

# Fresh CLI setup deliberately uses the deterministic/offline hash embedder,
# while the runtime resolver's no-config default remains ``auto`` for backward
# compatibility. Once written, this file is the canonical local configuration.
DEFAULT_CONFIG = {
    **RUNTIME_DEFAULTS,
    "embedder_mode": "hash",
}


# ── doctor ────────────────────────────────────────────────────────

def cmd_doctor(_args: argparse.Namespace) -> int:
    """Run system health checks."""
    checks: list[tuple[str, str, str]] = []
    ok = True

    # 1. Python version
    major, minor = sys.version_info[:2]
    if major >= 3 and minor >= 11:
        checks.append(("Python", "PASS", f"{sys.version.split()[0]}"))
    else:
        checks.append(("Python", "FAIL", f"{sys.version.split()[0]} (need >=3.11)"))
        ok = False

    # 2. Package import
    try:
        import server  # noqa: F401
        checks.append(("Package import", "PASS", ""))
    except ImportError as e:
        checks.append(("Package import", "FAIL", str(e)))
        ok = False

    # 3. Resolve the same runtime configuration used by API/MCP/CLI.
    try:
        runtime = resolve_runtime_config()
        db_path = runtime.database_path
    except Exception as exc:
        checks.append(("Runtime config", "FAIL", str(exc)))
        print("\n  LEVH Doctor")
        print("  " + "=" * 50)
        for name, status, detail in checks:
            detail_str = f"  {detail}" if detail else ""
            print(f"  {name:25s} {status:6s}{detail_str}")
        print("\n  Verdict: FAIL — fix the above issues before running.")
        return 1

    db_dir = os.path.dirname(os.path.abspath(db_path))
    if os.access(db_dir, os.W_OK):
        checks.append(("Database path", "PASS", db_dir))
    else:
        checks.append(("Database path", "FAIL", f"Not writable: {db_dir}"))
        ok = False

    # 4. Embedder mode
    requested_embedder_mode = runtime.embedder_mode
    try:
        from server.core.embedder import Embedder

        embedder = Embedder(requested_embedder_mode)
        identity = embedder.identity()
        provider = identity["provider"]
        if provider == "openai":
            route = "remote api.openai.com (explicit mode)"
        elif provider == "ollama":
            route = "local Ollama endpoint"
        else:
            route = "local/offline"
        detail = (
            f"requested={requested_embedder_mode}, effective={provider}, "
            f"model={identity['model']}, route={route}"
        )
        if embedder.fallback_reason:
            checks.append(("Embedder mode", "WARN", f"{detail}; {embedder.fallback_reason}"))
        else:
            checks.append(("Embedder mode", "PASS", detail))
    except Exception as e:
        checks.append(("Embedder mode", "FAIL", str(e)))
        ok = False

    # 5. API module import
    try:
        import server.api  # noqa: F401
        checks.append(("API import", "PASS", ""))
    except ImportError as e:
        checks.append(("API import", "FAIL", str(e)))
        ok = False

    # 6. MCP server module import
    try:
        import server.mcp_stdio  # noqa: F401
        checks.append(("MCP import", "PASS", ""))
    except ImportError as e:
        checks.append(("MCP import", "FAIL", str(e)))
        ok = False

    # 7. MCP SSE module import
    try:
        import server.mcp_sse  # noqa: F401
        checks.append(("MCP SSE import", "PASS", ""))
    except ImportError as e:
        checks.append(("MCP SSE import", "FAIL", str(e)))
        ok = False

    # 8. Packaged dashboard / frontend source exists
    frontend_dir = os.path.join(_REPO_ROOT, "frontend")
    dashboard_index = os.path.join(_REPO_ROOT, "server", "dashboard", "index.html")
    if os.path.isfile(dashboard_index):
        checks.append(("Dashboard bundle", "PASS", "packaged static UI present"))
    elif os.path.isdir(frontend_dir):
        checks.append(("Dashboard bundle", "WARN", "source present; packaged bundle missing"))
    else:
        checks.append(("Dashboard bundle", "FAIL", "dashboard and frontend source missing"))
        ok = False

    # 9. Configs module
    try:
        import server.configs  # noqa: F401
        checks.append(("Config generator", "PASS", ""))
    except ImportError as e:
        checks.append(("Config generator", "FAIL", str(e)))
        ok = False

    # 10. Canonical config source
    source = runtime.config_path or "defaults/environment"
    checks.append(("Runtime config", "PASS", f"source={source}"))

    # 11. MCP profile registry validity / counts
    try:
        from server.tools.profiles import DEFAULT_PROFILE, TOOL_TIERS, profile_counts

        counts = profile_counts()
        if not TOOL_TIERS or counts.get("full") != len(TOOL_TIERS):
            raise ValueError("profile registry count mismatch")
        checks.append(
            (
                "MCP profiles",
                "PASS",
                f"default={DEFAULT_PROFILE}; " + ", ".join(f"{k}={v}" for k, v in counts.items()),
            )
        )
    except Exception as e:
        checks.append(("MCP profiles", "FAIL", str(e)))
        ok = False

    # 12. Local dogfood state and canonical journal discovery
    try:
        from server.core.dogfood import dogfood_enabled, resolve_journal_path

        dogfood = dogfood_enabled()
        resolved = Path(resolve_journal_path(db_path=db_path))
        checks.append(
            (
                "Dogfood metrics",
                "PASS",
                f"{'ON' if dogfood else 'OFF (default)'}; journal={resolved.name}",
            )
        )
    except Exception as e:
        checks.append(("Dogfood metrics", "FAIL", str(e)))
        ok = False

    # 13. Database initialization / memory count. Zero memories are a WARN,
    # not a failure: the product is installed but still in first-run state.
    memory_count: int | None = None
    try:
        import sqlite3

        if not os.path.exists(db_path):
            checks.append(("Memory store", "WARN", "not initialized; run `levh setup`"))
        else:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            memory_count = int(row[0] if row else 0)
            if memory_count:
                checks.append(("Memory store", "PASS", f"{memory_count} memories"))
            else:
                checks.append(("Memory store", "WARN", "database ready; no memories yet"))
    except sqlite3.OperationalError:
        checks.append(("Memory store", "WARN", "database exists but schema is not initialized"))
    except Exception as e:
        checks.append(("Memory store", "FAIL", str(e)))
        ok = False

    # 14. Embedding compatibility. Mixed dimensions are safe (recall skips
    # incompatible vectors) but can silently hide old memories after a model
    # switch, so doctor makes the migration need explicit.
    try:
        import sqlite3

        dimension_counts: dict[int, int] = {}
        if os.path.exists(db_path):
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT embedding FROM memories WHERE embedding IS NOT NULL"
                ).fetchall()
            for (raw_embedding,) in rows:
                try:
                    dim = len(json.loads(raw_embedding))
                except Exception:
                    dim = -1
                dimension_counts[dim] = dimension_counts.get(dim, 0) + 1
        expected_dim = int(embedder.dimension)
        mismatched = {d: n for d, n in dimension_counts.items() if d != expected_dim}
        if mismatched:
            detail = ", ".join(f"{d}d={n}" for d, n in sorted(dimension_counts.items()))
            checks.append((
                "Embedding dimensions",
                "WARN",
                f"active={expected_dim}d; stored {detail}; re-embed before relying on complete recall",
            ))
        else:
            detail = "empty store" if not dimension_counts else f"{expected_dim}d={dimension_counts.get(expected_dim, 0)}"
            checks.append(("Embedding dimensions", "PASS", detail))
    except Exception as e:
        checks.append(("Embedding dimensions", "WARN", f"could not inspect: {e}"))

    # 15. SQLite operational contract. Connect only when a store already
    # exists so doctor does not create user data as a side effect.
    try:
        if os.path.exists(db_path):
            import asyncio

            from server.core.database import CURRENT_SCHEMA_VERSION, Database

            async def _sqlite_status() -> dict:
                database = Database(db_path)
                await database.connect()
                try:
                    return await database.runtime_status()
                finally:
                    await database.close()

            sqlite_status = asyncio.run(_sqlite_status())
            journal = str(sqlite_status.get("journal_mode", "")).lower()
            timeout_ms = int(sqlite_status.get("busy_timeout_ms", 0))
            schema = int(sqlite_status.get("schema_version", 0))
            fts = bool(sqlite_status.get("fts5_available"))
            level = (
                "PASS"
                if journal == "wal" and timeout_ms >= 5_000 and schema == CURRENT_SCHEMA_VERSION
                else "WARN"
            )
            checks.append(
                (
                    "SQLite runtime",
                    level,
                    f"journal={journal}; busy_timeout={timeout_ms}ms; "
                    f"schema={schema}/{CURRENT_SCHEMA_VERSION}; fts5={'on' if fts else 'off'}",
                )
            )
        else:
            checks.append(
                (
                    "SQLite runtime",
                    "PASS",
                    "new stores use WAL, busy_timeout=5000ms, numbered migrations and FTS5 when available",
                )
            )
    except Exception as e:
        checks.append(("SQLite runtime", "WARN", f"could not inspect: {e}"))

    recommendation = (
        "run `levh setup --demo --client claude --profile work`"
        if not memory_count
        else "generate an MCP config and test recall"
    )
    checks.append(("Onboarding", "PASS", recommendation))

    # Print report
    print("\n  LEVH Doctor")
    print("  " + "=" * 50)
    for name, status, detail in checks:
        detail_str = f"  {detail}" if detail else ""
        print(f"  {name:25s} {status:6s}{detail_str}")
    print()

    if ok:
        print("  Verdict: OK")
    else:
        print("  Verdict: FAIL — fix the above issues before running.")

    return 0 if ok else 1


# ── setup / onboarding ───────────────────────────────────────────

def cmd_setup(args: argparse.Namespace) -> int:
    """First-run setup: initialize storage, choose demo/real mode, generate
    one focused MCP config, and write a privacy-safe local receipt."""
    import asyncio

    from server.configs import generate_config, normalize_platform
    from server.core import engine_provider
    from server.core.dogfood import dogfood_enabled
    from server.core.onboarding import write_receipt
    from server.tools.profiles import UnknownProfileError, resolve_profile

    if args.status:
        async def _status() -> dict:
            engine = engine_provider.get_engine()
            await engine.initialize()
            try:
                return await engine.onboarding_status()
            finally:
                await engine.shutdown()

        print(json.dumps(asyncio.run(_status()), indent=2, ensure_ascii=False))
        return 0

    mode = "demo" if args.demo else "real" if args.real else ""
    if not mode:
        if sys.stdin.isatty():
            choice = input("  Choose setup mode: [d]emo or [r]eal data? ").strip().lower()
            mode = "demo" if choice in {"d", "demo"} else "real" if choice in {"r", "real"} else ""
        if not mode:
            print("  Choose one: --demo or --real", file=sys.stderr)
            return 1

    try:
        platform = normalize_platform(args.client)
        profile = resolve_profile(args.profile)
    except (ValueError, UnknownProfileError) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    # Initialize local config without ever overwriting an existing one.
    config_dir = Path(CONFIG_DIR)
    config_file = config_dir / CONFIG_FILE
    mcp_dir = config_dir / MCP_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    if not config_file.exists():
        cfg = dict(DEFAULT_CONFIG)
        if os.getenv("SQLITE_DB_PATH"):
            cfg["database_path"] = os.environ["SQLITE_DB_PATH"]
        if os.getenv("EMBEDDER_MODE"):
            cfg["embedder_mode"] = os.environ["EMBEDDER_MODE"]
        config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    # The config file may have been created in this process. Ensure a cached
    # test/embedded engine cannot retain the pre-setup path.
    engine_provider.set_engine(None)
    runtime = resolve_runtime_config()

    async def _run() -> tuple[dict, dict]:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            seed_result = {"seeded": 0, "skipped": False}
            if mode == "demo":
                seed_result = await engine.seed_demo(force=False)
            status = await engine.onboarding_status()
            return seed_result, status
        finally:
            await engine.shutdown()

    seed_result, status = asyncio.run(_run())

    generated = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )
    config_path = mcp_dir / f"{args.client}-{profile}.json"
    config_path.write_text(json.dumps(generated, indent=2) + "\n", encoding="utf-8")

    receipt = write_receipt(
        database_ready=True,
        first_memory_ready=bool(status.get("memory_count")),
        mcp_client=args.client,
        mcp_profile=profile,
        demo_mode=mode == "demo",
        dogfood_enabled=dogfood_enabled(),
    )

    print("\n  LEVH setup")
    print("  " + "=" * 50)
    print(f"  Mode:          {mode}")
    print(f"  Database:      ready ({status.get('memory_count', 0)} memories)")
    if mode == "demo":
        if seed_result.get("skipped"):
            print("  Demo data:     skipped (store already contains data)")
        else:
            print(f"  Demo data:     {seed_result.get('seeded', 0)} memories loaded")
    else:
        print("  Demo data:     not loaded")
    print(f"  MCP client:    {args.client}")
    print(f"  MCP profile:   {profile}")
    print(f"  MCP config:    {config_path}")
    print(f"  Dogfood:       {'ON' if receipt['dogfood_enabled'] else 'OFF (local default)'}")
    print("\n  MCP profiles narrow tool discovery; they are not an authorization boundary.")
    if mode == "real" and not status.get("memory_count"):
        print('  Next: levh capture "Atlas uses PostgreSQL in production."')
    print("  Next: levh serve")
    print("  Then test recall from your configured AI client.")
    return 0


# ── init ──────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    """Create local config directory and default config file."""
    config_dir = Path(CONFIG_DIR)
    config_file = config_dir / CONFIG_FILE
    mcp_dir = config_dir / MCP_DIR

    if config_file.exists() and not args.force:
        print(f"  Config already exists: {config_file}")
        print(f"  Use --force to overwrite.")
        return 1

    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_dir.mkdir(parents=True, exist_ok=True)

    cfg = dict(DEFAULT_CONFIG)
    if args.embedder_mode:
        cfg["embedder_mode"] = args.embedder_mode
    if args.db_path:
        cfg["database_path"] = args.db_path

    config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"  Created: {config_file}")
    print(f"  Created: {mcp_dir}/")
    print(f"  Config:  embedder_mode={cfg['embedder_mode']}, "
          f"database_path={cfg['database_path']}")
    return 0


# ── serve ────────────────────────────────────────────────────────

def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the FastAPI server via uvicorn."""
    import uvicorn
    runtime = resolve_runtime_config(
        explicit={"api_host": args.host, "api_port": args.port}
    )
    host = runtime.api_host
    port = runtime.api_port
    import ipaddress

    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback and not get_env("LEVH_TOKEN", "").strip():
        print(
            "  Refusing non-loopback bind without LEVH_TOKEN. "
            "Set a strong token or bind to 127.0.0.1.",
            file=sys.stderr,
        )
        return 1
    print(f"  Starting LEVH API on {host}:{port}")
    print(f"  Dashboard: http://{host}:{port}/   API docs: http://{host}:{port}/docs")
    uvicorn.run("server.api:app", host=host, port=port, reload=args.reload)
    return 0


# ── capture ──────────────────────────────────────────────────────

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


# ── sync (Connector Framework v2) ────────────────────────────────

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
            print(f"  Ignoring malformed --config value (expected KEY=VALUE): {kv}", file=sys.stderr)
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

    try:
        result = asyncio.run(_run())
    except KeyError as e:
        print(f"  {e}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, ConnectionError) as e:
        print(f"  Connection failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"  Sync failed: {e}", file=sys.stderr)
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


# ── context (CLAUDE.md / .cursorrules generation) ────────────────

def cmd_context(args: argparse.Namespace) -> int:
    """Generate a context file from memories and print or write it."""
    import asyncio

    from server.core import engine_provider

    project = args.project or _detect_project()

    async def _run() -> str:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.generate_context_file(
                project=project, style=args.style
            )
        finally:
            await engine.shutdown()

    content = asyncio.run(_run())

    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"  Wrote {args.output} ({len(content)} chars, project={project or 'all'})")
    else:
        print(content)
    return 0


# ── hook install (git auto-capture) ──────────────────────────────

_HOOK_MARKER = "# levh-hook"

_HOOK_TEMPLATE = """#!/bin/sh
{marker}
# Auto-capture the latest commit message into LEVH.
# Installed by `levh hook install`. Remove with `levh hook uninstall`.
MSG=$(git log -1 --pretty=%B)
HASH=$(git log -1 --pretty=%h)
{python} -m server.cli capture "commit ${{HASH}}: ${{MSG}}" --source git-hook --tags git,commit >/dev/null 2>&1 || true
"""


def _hooks_dir() -> Path | None:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()) / "hooks"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def cmd_hook(args: argparse.Namespace) -> int:
    """Install/uninstall the git post-commit auto-capture hook."""
    hooks = _hooks_dir()
    if hooks is None:
        print("  Not inside a git repository.", file=sys.stderr)
        return 1
    hook_file = hooks / "post-commit"

    if args.hook_command == "uninstall":
        if hook_file.exists() and _HOOK_MARKER in hook_file.read_text(encoding="utf-8"):
            hook_file.unlink()
            print(f"  Removed {hook_file}")
            return 0
        print("  No LEVH hook installed.")
        return 0

    # install
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8")
        if _HOOK_MARKER in existing:
            print(f"  Hook already installed: {hook_file}")
            return 0
        print(f"  A post-commit hook already exists: {hook_file}", file=sys.stderr)
        print("  Append the LEVH capture line manually, or remove it first.", file=sys.stderr)
        return 1

    hooks.mkdir(parents=True, exist_ok=True)
    hook_file.write_text(
        _HOOK_TEMPLATE.format(marker=_HOOK_MARKER, python=sys.executable),
        encoding="utf-8",
    )
    hook_file.chmod(0o755)
    print(f"  Installed post-commit hook: {hook_file}")
    print("  Every commit message will now be captured as a memory (source=git-hook).")
    return 0


# ── summarize (session auto-capture) ─────────────────────────────

def cmd_summarize(args: argparse.Namespace) -> int:
    """Distill a session's memories into one durable summary memory."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> int:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            session = await engine.get_session(args.session_id)
            if not session:
                print(f"  Session not found: {args.session_id}", file=sys.stderr)
                return 1
            summary = await engine.summarize_session(args.session_id)
            if not summary:
                print(f"  Nothing to summarize — session {args.session_id} has no memories.")
                return 0
            print(f"  Summarized session '{session.name}' into memory {summary.id[:8]}...")
            print(f"\n{summary.content}")
            return 0
        finally:
            await engine.shutdown()

    return asyncio.run(_run())


# ── benchmark (recall quality) ────────────────────────────────────

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


# ── tune (offline H-score weight fitting) ─────────────────────────

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


# ── review (spaced-repetition) ────────────────────────────────────

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


# ── entities (persistent entity knowledge graph) ─────────────────

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


# ── trust (provenance / trust score) ─────────────────────────────

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


# ── conflicts (deterministic conflict-candidate review) ──────────

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


# ── hard-delete + redaction audit ────────────────────────────────

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
        secrets = ", ".join(item["secrets"])
        print(f"  [{item['id'][:8]}] {secrets} — {item['preview']}")
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


# ── seed-demo (onboarding) ───────────────────────────────────────

def cmd_seed_demo(args: argparse.Namespace) -> int:
    """Populate an empty store with a deterministic demo corpus so a first run
    shows a live dashboard instead of empty states."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.seed_demo(force=args.force)
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    if result.get("skipped"):
        print(
            f"  Store already has {result.get('existing', 0)} memories — nothing seeded.\n"
            "  Re-run with --force to add the demo data anyway."
        )
        return 0
    print(
        f"  Seeded {result['seeded']} demo memories -> "
        f"{result['entities']} entities ({result['entity_links']} links), "
        f"{result['trust_scored']} trust-scored, "
        f"{result['conflict_candidates']} conflict candidate(s)."
    )
    print("  Open the dashboard (`levh serve`) to explore it.")
    return 0


def cmd_export_full(args: argparse.Namespace) -> int:
    """Export memories + entity graph + trust scores + conflicts to one file."""
    import asyncio

    from server.core import engine_provider

    fmt = args.format
    out_path = args.out or f"levh-full-export.{fmt}"

    async def _run():
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            from server.core.full_export import (
                PdfUnavailableError,
                build_full_export,
                export_full_sqlite,
                render_full_export_pdf,
            )

            if fmt == "json":
                import json

                export = await build_full_export(engine)
                with open(out_path, "w") as f:
                    json.dump(export, f, indent=2, default=str)
                return export["counts"]
            elif fmt == "sqlite":
                blob = await export_full_sqlite(engine)
                with open(out_path, "wb") as f:
                    f.write(blob)
                return None
            else:
                export = await build_full_export(engine)
                try:
                    blob = render_full_export_pdf(export)
                except PdfUnavailableError as exc:
                    print(f"  {exc}", file=sys.stderr)
                    return None
                with open(out_path, "wb") as f:
                    f.write(blob)
                return export["counts"]
        finally:
            await engine.shutdown()

    counts = asyncio.run(_run())
    if counts is None and fmt == "pdf":
        return 1
    print(f"  Wrote {out_path}" + (f" — {counts}" if counts else ""))
    return 0


def cmd_remove_demo(args: argparse.Namespace) -> int:
    """Remove all demo-tagged memories, leaving real data untouched."""
    import asyncio

    from server.core import engine_provider

    async def _run() -> dict:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.remove_demo_data()
        finally:
            await engine.shutdown()

    result = asyncio.run(_run())
    removed = result.get("removed", 0)
    if removed == 0:
        print("  No demo data found — nothing to remove.")
    else:
        print(f"  Removed {removed} demo memories.")
    return 0


# ── continue (autonomous session continuity) ────────────────────────

def cmd_continue(args: argparse.Namespace) -> int:
    """Show context to resume work — synthesizes session DNA from recent activity."""
    import asyncio

    from server.core import engine_provider

    project = args.project or _detect_project()

    async def _run() -> str:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            return await engine.get_continuity_context(
                task=args.task or None,
                project=project or None,
                limit=args.limit,
                since=args.since or None,
            )
        finally:
            await engine.shutdown()

    try:
        context = asyncio.run(_run())
        print(context)
        return 0
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return 1


# ── mcp config ───────────────────────────────────────────────────

def cmd_mcp_config(args: argparse.Namespace) -> int:
    """Print MCP client configuration JSON to stdout."""
    from server.configs import generate_config, normalize_platform

    try:
        platform = normalize_platform(args.platform)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    from server.tools.profiles import (
        UnknownProfileError,
        resolve_profile,
        tools_for_profile,
    )

    try:
        profile = resolve_profile(getattr(args, "profile", None))
    except UnknownProfileError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    runtime = resolve_runtime_config(
        explicit={
            "embedder_mode": args.embedder_mode,
            "database_path": args.db_path,
        }
    )
    cfg = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )
    print(json.dumps(cfg, indent=2))
    # Report the surface on stderr so stdout stays a clean, pipeable JSON blob.
    n = len(tools_for_profile(profile))
    print(
        f"  MCP profile '{profile}' → {n} tools advertised "
        f"(change with --profile minimal|work|admin|full)",
        file=sys.stderr,
    )
    return 0


# ── mcp profiles ──────────────────────────────────────────────────

def cmd_mcp_profiles(_args: argparse.Namespace) -> int:
    """List the MCP tool profiles and how many tools each advertises."""
    from server.tools.profiles import (
        DEFAULT_PROFILE,
        profile_counts,
        tools_for_profile,
    )

    counts = profile_counts()
    print("\n  MCP tool profiles (advertise fewer tools = better tool selection)")
    print("  " + "=" * 52)
    for name, count in counts.items():
        marker = "  (default)" if name == DEFAULT_PROFILE else ""
        print(f"  {name:8s} {count:3d} tools{marker}")
    print("  " + "=" * 52)
    print("  minimal ⊂ work ⊂ admin ⊂ full")
    print(f"  Set LEVH_MCP_PROFILE or `mcp config --profile <name>`.\n")
    # Show the minimal set explicitly — it's short and clarifies the core loop.
    print("  minimal tools: " + ", ".join(sorted(tools_for_profile("minimal"))))
    return 0


# ── eval (2.25 memory evaluation) ─────────────────────────────────

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


# ── dogfood (2.25 local usage journal) ────────────────────────────

def cmd_dogfood_status(args: argparse.Namespace) -> int:
    """Show aggregate stats from the local dogfood journal."""
    import json

    from server.core.dogfood import DogfoodJournal, resolve_journal_path

    path = resolve_journal_path(
        explicit_path=args.journal or None,
        db_path=os.getenv("SQLITE_DB_PATH"),
    )
    status = DogfoodJournal(path).status()
    print(json.dumps(status, indent=2, ensure_ascii=False))
    if status["total_events"] == 0:
        print("\n  Journal is empty — dogfood collection is local-only and "
              "opt-in; nothing is recorded (or sent anywhere) by default.")
    return 0


def cmd_dogfood_export(args: argparse.Namespace) -> int:
    """Explicit user action: write the aggregate dogfood report to a file."""
    from server.core.dogfood import DogfoodJournal, resolve_journal_path

    path = resolve_journal_path(
        explicit_path=args.journal or None,
        db_path=os.getenv("SQLITE_DB_PATH"),
    )
    report = DogfoodJournal(path).export(args.output)
    print(f"Aggregate dogfood report ({report['total_events']} events) → {args.output}")
    print("Raw event lines stay in the local journal; only aggregates were exported.")
    return 0


# ── mcp stdio ─────────────────────────────────────────────────────

def cmd_mcp_stdio(_args: argparse.Namespace) -> int:
    """Launch the MCP stdio server (wraps server.mcp_stdio)."""
    from server.mcp_stdio import mcp
    mcp.run(transport="stdio")
    return 0


# ── main ────────────────────────────────────────────────────────

def main() -> int:
    invoked_as = Path(sys.argv[0]).stem.lower()
    legacy_invocation = invoked_as == "stackmemory"
    if legacy_invocation:
        print("'stackmemory' is deprecated; use 'levh'", file=sys.stderr)
    parser = argparse.ArgumentParser(
        prog="stackmemory" if legacy_invocation else "levh",
        description="LEVH - Local-first memory layer for AI agents and humans",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 2.27.2")
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
    hook_p = sub.add_parser("hook", help="Git auto-capture hook")
    hook_sub = hook_p.add_subparsers(dest="hook_command", help="Hook subcommands")
    hook_sub.add_parser("install", help="Install post-commit capture hook")
    hook_sub.add_parser("uninstall", help="Remove the capture hook")

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
        review_p.print_help()
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
        ent_p.print_help()
        return 1
    elif args.command == "trust":
        if args.trust_command in ("show", "recompute", "low"):
            return cmd_trust(args)
        trust_p.print_help()
        return 1
    elif args.command == "conflicts":
        if args.conflicts_command in ("detect", "list", "review"):
            return cmd_conflicts(args)
        conf_p.print_help()
        return 1
    elif args.command == "hook":
        if args.hook_command in ("install", "uninstall"):
            return cmd_hook(args)
        hook_p.print_help()
        return 1
    elif args.command == "mcp":
        if args.mcp_command == "config":
            return cmd_mcp_config(args)
        elif args.mcp_command == "profiles":
            return cmd_mcp_profiles(args)
        elif args.mcp_command == "stdio":
            return cmd_mcp_stdio(args)
        else:
            mcp_p.print_help()
            return 1
    elif args.command == "eval":
        if args.eval_command == "run":
            return cmd_eval_run(args)
        elif args.eval_command == "report":
            return cmd_eval_report(args)
        eval_p.print_help()
        return 1
    elif args.command == "dogfood":
        if args.dogfood_command == "status":
            return cmd_dogfood_status(args)
        elif args.dogfood_command == "export":
            return cmd_dogfood_export(args)
        dog_p.print_help()
        return 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
