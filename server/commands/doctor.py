"""The `levh doctor` health check."""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import os
import sys
from pathlib import Path

from server.commands.paths import _REPO_ROOT
from server.core.db.aggregates import AggregateQueries
from server.core.env import get_env
from server.core.runtime_config import configured_api_port
from server.core.runtime_config import resolve_runtime_config
from server.core.runtime_config import configured_bind_host


#: How long a doctor repair waits for a store another process is writing to.
#: A live server holds this database; a repair that blocked on it would hang
#: the very command an operator runs to find out what is wrong.
_DB_BUSY_TIMEOUT_SECONDS = 5.0


def _running_bind_host(runtime) -> str | None:
    """Ask a live server what address it is bound to, or ``None`` if silent.

    The serving process is the only authority on its own socket, and it may have
    been started by a stub like the Dockerfile's uvicorn call that never passes
    through ``cmd_serve`` — so argv here is not necessarily even its argv.
    ``/api/health`` is unauthenticated by design, which is what lets this probe
    work without the token whose presence it is trying to assess.

    Probed over loopback whatever the configured bind: a wildcard bind still
    answers there, and this host may have no route to the advertised address.
    A short timeout keeps a silent port from stalling the check.

    The port is resolved like the bind host is — ``--port`` in argv first — and
    each fallback is tried in turn. A server started with
    ``levh serve --port 9000`` while ``API_PORT``/config still say ``8000``
    would otherwise never be found, and the check would report a boundary that
    is not the one in force (issue #170).
    """
    import json
    import urllib.request

    for port in _candidate_ports(runtime):
        try:
            with urllib.request.urlopen(  # nosec B310 - fixed loopback http URL, no user input
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - an unreachable candidate port is simply the next candidate
            continue
        host = str(payload.get("api_host") or "").strip()
        if host:
            return host
    return None


def _candidate_ports(runtime) -> list[int]:
    """Ports worth probing, most specific first, each at most once."""
    candidates: list[int] = [configured_api_port()]
    configured = getattr(runtime, "api_port", None)
    if isinstance(configured, int):
        candidates.append(configured)
    for port in (8000, 9000):
        candidates.append(port)
    unique: list[int] = []
    for port in candidates:
        if isinstance(port, int) and 1 <= port <= 65535 and port not in unique:
            unique.append(port)
    return unique



def _quarantined_rowids(db_path: str) -> list[int]:
    """Rowids of stored rows the current model cannot accept.

    Mirrors the read-time quarantine in ``server.core.episodic`` on a raw
    sqlite3 connection so ``levh doctor`` reports the loss without starting
    the engine or touching a live server. The row is decoded with the same
    ``row_to_memory_dict`` the query layer uses before the model sees it —
    checking raw columns instead counted every NULL ``metadata`` as a broken
    row, which is how 2 unreachable rows reported as 18.

    Rowids, not ids, are what makes the list actionable: the rows named here are
    exactly the ones that may have no id to name them by, and a rowid is the one
    address SQLite gives a row that cannot answer to its own primary key.
    """
    import sqlite3

    from server.core.db.memories import row_to_memory_dict
    from server.core.types import Memory

    rowids: list[int] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT rowid, * FROM memories"):
            # ``rowid`` is deliberately kept out of the mapping handed to the
            # model: it is a storage address, not a ``Memory`` field, and
            # passing it through would quarantine every row on a model that
            # refuses unknown keys.
            columns = {key: row[key] for key in row.keys() if key != "rowid"}
            try:
                Memory(**row_to_memory_dict(columns))
            except Exception:  # noqa: BLE001 - any rejected row is quarantined
                rowids.append(int(row["rowid"]))
    return rowids


def _count_quarantined_rows(db_path: str) -> int:
    """How many stored rows the current model cannot accept."""
    return len(_quarantined_rowids(db_path))


def _quarantine_detail(db_path: str, rowids: list[int]) -> str:
    """One line naming the quarantined rows, and how to repair what can be.

    The count alone told an operator memories were being lost and gave them
    nothing to do about it. Rowids make the rows findable in the file, and a row
    that only lacks an id is the one case the store can repair itself, so the
    line ends with the command that does it.
    """
    named = ", ".join(str(rowid) for rowid in rowids[:5])
    if len(rowids) > 5:
        named += f", +{len(rowids) - 5} more"
    detail = f"{len(rowids)} row(s) this build rejects; recall skips them (rowid {named})"
    try:
        idless = _idless_rowids(db_path)
    except Exception:  # noqa: BLE001 - the repair hint is optional, the count is not
        return detail
    if idless:
        detail += f"; {len(idless)} without an id - repair with `levh doctor --fix-ids`"
    return detail


def _idless_rowids(db_path: str) -> list[int]:
    """Rowids whose ``id`` is NULL or blank — the quarantine this one repairs.

    ``memories_integrity_id_ai`` has refused such writes since #267, so these
    rows were stored before the trigger existed (or by a tool that dropped it).
    They are the one quarantine reason fixable without inventing content: give
    the row an id and the model accepts it, so ``levh doctor --fix-ids`` can act
    on the diagnosis instead of only reporting it.
    """
    import sqlite3

    with sqlite3.connect(db_path, timeout=_DB_BUSY_TIMEOUT_SECONDS) as conn:
        cursor = conn.execute(
            "SELECT rowid FROM memories "
            "WHERE id IS NULL OR length(trim(id)) = 0 ORDER BY rowid"
        )
        return [int(row[0]) for row in cursor.fetchall()]


def _recover_idless_rows(db_path: str) -> list[tuple[int, str]]:
    """Give every id-less row a generated id; return the ``(rowid, id)`` pairs.

    Only ``id`` is written — the content, timestamps and scores a memory still
    has are left exactly as they were, which is what keeps this a repair rather
    than a deletion. The FTS entry the corrupt write left behind is keyed on the
    NULL it was inserted with, so it is dropped here too: nothing can join back
    to it, and leaving it means a text hit that resolves to no memory.
    """
    import sqlite3
    import uuid

    assigned = [(rowid, uuid.uuid4().hex) for rowid in _idless_rowids(db_path)]
    if not assigned:
        return []
    with sqlite3.connect(db_path, timeout=_DB_BUSY_TIMEOUT_SECONDS) as conn:
        conn.executemany(
            "UPDATE memories SET id = ? WHERE rowid = ?",
            [(memory_id, rowid) for rowid, memory_id in assigned],
        )
        fts_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories_fts'"
        ).fetchone()
        if fts_exists:
            conn.execute("DELETE FROM memories_fts WHERE memory_id IS NULL")
        conn.commit()
    return assigned


def _run_coroutine_blocking(coro):
    """Run ``coro`` to completion from a synchronous caller.

    ``asyncio.run`` raises ``RuntimeError`` when a loop is already running on
    this thread — doctor is a sync function, but tests (and any async caller)
    invoke it from within one. Rather than lose the check to that error, hand
    the coroutine to a worker thread that owns a fresh loop (issue #271).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def cmd_doctor(args: argparse.Namespace) -> int:
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
        import server
        checks.append(("Package import", "PASS", ""))
    except ImportError as e:
        checks.append(("Package import", "FAIL", str(e)))
        ok = False

    # 3. Resolve the same runtime configuration used by API/MCP/CLI.
    try:
        runtime = resolve_runtime_config()
        db_path = runtime.database_path
    except Exception as exc:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
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
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("Embedder mode", "FAIL", str(e)))
        ok = False

    # 5. API module import
    try:
        import server.api
        checks.append(("API import", "PASS", ""))
    except ImportError as e:
        checks.append(("API import", "FAIL", str(e)))
        ok = False

    # 6. MCP server module import
    try:
        import server.mcp_stdio
        checks.append(("MCP import", "PASS", ""))
    except ImportError as e:
        checks.append(("MCP import", "FAIL", str(e)))
        ok = False

    # 7. MCP SSE module import
    try:
        import server.mcp_sse
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
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
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
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("Dogfood metrics", "FAIL", str(e)))
        ok = False

    # 12b. A repair, and only when it was asked for. Rows that merely lack an id
    # are given one back before the store is inspected below, so the report
    # describes the store as this command just left it. Without the flag doctor
    # writes nothing, which is what keeps it safe to run against a live server.
    if getattr(args, "fix_ids", False):
        try:
            recovered = _recover_idless_rows(db_path)
        except Exception as e:  # noqa: BLE001 - a failed repair is reported, never fatal
            checks.append(("Repaired ids", "FAIL", str(e)))
            ok = False
        else:
            if recovered:
                assigned = ", ".join(
                    f"rowid {rowid} -> {memory_id[:8]}" for rowid, memory_id in recovered
                )
                checks.append(("Repaired ids", "PASS", f"{len(recovered)} row(s) given an id: {assigned}"))
            else:
                checks.append(("Repaired ids", "PASS", "no row was missing an id"))

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
            # Quarantined rows (#267 follow-up): rows this build's model
            # rejects are skipped at read time and named in a warning, so a
            # corrupting writer no longer takes the API down — but the skip is
            # otherwise invisible. Counting the rows the model cannot accept
            # makes the loss observable where operators already look.
            quarantined_rowids = _quarantined_rowids(db_path)
            quarantined = len(quarantined_rowids)
            quarantine_note = f"; {quarantined} quarantined row(s) skipped on read" if quarantined else ""
            if memory_count:
                checks.append(("Memory store", "PASS", f"{memory_count} memories"))
                if quarantined:
                    checks.append(("Quarantined rows", "WARN", _quarantine_detail(db_path, quarantined_rowids)))
                    ok = False
            else:
                checks.append(("Memory store", "WARN", "database ready; no memories yet" + quarantine_note))
    except sqlite3.OperationalError:
        checks.append(("Memory store", "WARN", "database exists but schema is not initialized"))
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("Memory store", "FAIL", str(e)))
        ok = False

    # 14. Embedding compatibility. Mixed dimensions are safe (recall skips
    # incompatible vectors) but can silently hide old memories after a model
    # switch, so doctor makes the migration need explicit. Dimension counting
    # lives in one place: AggregateQueries.embedding_dimension_counts.
    try:
        dimension_counts: dict[int, int] = {}
        if os.path.exists(db_path):
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT embedding FROM memories WHERE embedding IS NOT NULL"
                ).fetchall()
            dimension_counts = AggregateQueries.dimension_counts_from_rows(rows)
        expected_dim = int(embedder.dimension)
        mismatched = {d: n for d, n in dimension_counts.items() if d != expected_dim}
        if mismatched:
            detail = ", ".join(f"{d}d={n}" for d, n in sorted(dimension_counts.items()))
            checks.append((
                "Embedding dimensions",
                "WARN",
                f"active={expected_dim}d; stored {detail}; run `levh reembed` before relying on complete recall",
            ))
        else:
            detail = "empty store" if not dimension_counts else f"{expected_dim}d={dimension_counts.get(expected_dim, 0)}"
            checks.append(("Embedding dimensions", "PASS", detail))
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("Embedding dimensions", "WARN", f"could not inspect: {e}"))

    # 15. SQLite operational contract. Connect only when a store already
    # exists so doctor does not create user data as a side effect.
    try:
        if os.path.exists(db_path):
            from server.core.database import CURRENT_SCHEMA_VERSION, Database

            async def _sqlite_status() -> dict:
                database = Database(db_path)
                await database.connect()
                try:
                    return await database.runtime_status()
                finally:
                    await database.close()

            sqlite_status = _run_coroutine_blocking(_sqlite_status())
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
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("SQLite runtime", "WARN", f"could not inspect: {e}"))

    # 16. Remote access boundary. The tokenless override exists for Docker,
    # where the host's traffic arrives from the bridge gateway rather than a
    # loopback peer; it is safe only while the bind itself stays private. The
    # issue this addresses (#151): the override's sole defence was a YAML
    # comment, so a one-line publish change turned it into anonymous remote
    # access. Doctor now refuses the combination outright instead of leaving
    # it to a comment nobody re-reads.
    try:
        from server.auth import (
            ALLOW_REMOTE_WITHOUT_TOKEN_ENV,
            _is_loopback,
            unauthenticated_remote_access_enabled,
        )

        token = get_env("LEVH_TOKEN", "").strip()
        if not unauthenticated_remote_access_enabled(token):
            detail = (
                "LEVH_TOKEN set; tokenless peers refused"
                if token
                else "loopback-only without LEVH_TOKEN"
            )
            checks.append(("Remote access", "PASS", detail))
        else:
            # The override is in effect, so the bind decides whether this is a
            # warning or a hole — and that is the one case where the answer must
            # be right. Config alone cannot give it: `levh serve --host 0.0.0.0`
            # and the Dockerfile's uvicorn bind every interface while config
            # still holds the 127.0.0.1 default, so the check used to pass on
            # exactly the topology it exists to catch (issue #156). Ask the
            # running server first; when nothing answers, fall back to argv then
            # config.
            bind_host = _running_bind_host(runtime) or configured_bind_host()
            if not _is_loopback(bind_host):
                checks.append(
                    (
                        "Remote access",
                        "FAIL",
                        f"{ALLOW_REMOTE_WITHOUT_TOKEN_ENV}=true with non-loopback bind "
                        f"{bind_host}: unauthenticated remote access. Set LEVH_TOKEN, "
                        f"or remove the override and bind to 127.0.0.1.",
                    )
                )
                ok = False
            else:
                checks.append(
                    (
                        "Remote access",
                        "WARN",
                        f"{ALLOW_REMOTE_WITHOUT_TOKEN_ENV}=true: unauthenticated non-loopback "
                        f"peers accepted; safe only while {bind_host} stays private",
                    )
                )
    except Exception as e:  # noqa: BLE001 - a failed check is reported as FAIL, never fatal
        checks.append(("Remote access", "FAIL", str(e)))
        ok = False

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
