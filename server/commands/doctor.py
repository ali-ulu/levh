"""The `levh doctor` health check."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from server.commands.paths import _REPO_ROOT
from server.core.runtime_config import resolve_runtime_config



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
