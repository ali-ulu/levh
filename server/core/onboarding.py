"""First-run onboarding and local setup receipts (2.26).

This module computes readiness from real engine state. It stores only local,
non-sensitive setup metadata: never memory content, queries, tokens, API keys,
email addresses, or absolute private paths.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any

from server.configs import PLATFORM_ALIASES, PLATFORMS
from server.core.env import get_env
from server.tools.profiles import DEFAULT_PROFILE, profile_counts

ONBOARDING_VERSION = "onboarding-v1"
RECEIPT_ENV = "LEVH_ONBOARDING_RECEIPT_PATH"
DEFAULT_RECEIPT_PATH = ".stackmemory/onboarding-receipt.json"


def levh_version() -> str:
    try:
        return package_version("levh")
    except PackageNotFoundError:
        return get_env("LEVH_VERSION", "unknown")


def _home_receipt_path() -> Path:
    home = Path.home() / ".stackmemory" / "onboarding-receipt.json"
    home.parent.mkdir(parents=True, exist_ok=True)
    return home


def _is_writable(directory: Path) -> bool:
    """Prove a directory is writable by writing to it.

    ``os.access`` answers what the permission bits say, which is a different
    question: it does not account for Windows ACLs, a read-only mount, a
    container's user mapping, or an immutable flag. The only reliable check for
    "can this process create a file here" is to create one.
    """
    probe = directory / f".levh-write-probe-{os.getpid()}"
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        # Writability is already proven; a probe file left behind is untidy,
        # not a reason to declare the directory unusable.
        pass
    return True


def _default_receipt_path() -> Path:
    """Resolve the default receipt location, preferring a writable directory.

    Happy path: the project-local ``.stackmemory`` next to the current working
    directory. But the process cwd may be read-only (for example the API server
    started from a system directory such as ``C:\\Windows\\System32``), which
    would make ``write_receipt`` raise PermissionError and turn a harmless
    onboarding endpoint into an HTTP 500. In that case fall back to the user
    config directory (``~/.stackmemory``), which is always writable for the
    user, so onboarding never fails purely because of a non-writable cwd.

    Creatability is not writability. ``mkdir(parents=True, exist_ok=True)``
    succeeds as a no-op against a ``.stackmemory`` that already exists and
    cannot be written to, so the old check passed and handed back a path the
    next ``write_text`` would fail on — the fallback this function exists for
    never ran.
    """
    local = Path(DEFAULT_RECEIPT_PATH)
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        if _is_writable(local.parent):
            return local
    except OSError:
        pass
    return _home_receipt_path()


def _explicitly_requested(explicit_path: str | os.PathLike | None) -> bool:
    """True when the caller (or the environment) named the receipt location."""
    return explicit_path is not None or bool(get_env(RECEIPT_ENV, "").strip())


def receipt_path(explicit_path: str | os.PathLike | None = None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)
    explicit = get_env(RECEIPT_ENV, "").strip()
    if explicit:
        return Path(explicit)
    return _default_receipt_path()


def read_receipt(path: str | os.PathLike | None = None) -> dict[str, Any] | None:
    target = receipt_path(path)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def write_receipt(
    *,
    database_ready: bool,
    first_memory_ready: bool,
    mcp_client: str | None,
    mcp_profile: str,
    demo_mode: bool,
    dogfood_enabled: bool,
    path: str | os.PathLike | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Write a privacy-safe local onboarding receipt."""
    receipt = {
        "onboarding_version": ONBOARDING_VERSION,
        "levh_version": levh_version(),
        "database_ready": bool(database_ready),
        "first_memory_ready": bool(first_memory_ready),
        "mcp_client": mcp_client,
        "mcp_profile": mcp_profile,
        "demo_mode": bool(demo_mode),
        "dogfood_enabled": bool(dogfood_enabled),
        "completed_at": completed_at or datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
    target = receipt_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return receipt
    except OSError:
        # The probe in _default_receipt_path narrows this window; it does not
        # close it. A directory can lose its permissions between the check and
        # the write, and a probe cannot predict a full disk.
        if _explicitly_requested(path):
            # A caller who named the path gets the error. Quietly writing the
            # receipt somewhere else would leave them looking for a file that
            # is not where they asked for it.
            raise
        fallback = _home_receipt_path()
        if fallback == target:
            # Already the fallback. Re-raising keeps the failure visible
            # instead of pretending a receipt was written.
            raise
        fallback.write_text(body, encoding="utf-8")
        return receipt


def public_client_options() -> list[dict[str, str]]:
    """Authoritative client list without duplicate aliases."""
    preferred = (
        "claude",
        "claude_code",
        "cursor",
        "windsurf",
        "vscode",
        "cline",
        "jcode",
        "omp",
        "opencode",
        "codex",
        "hermes",
    )
    result: list[dict[str, str]] = []
    for alias in preferred:
        platform = PLATFORM_ALIASES[alias]
        result.append(
            {
                "id": alias,
                "platform": platform,
                "description": PLATFORMS[platform]["description"],
            }
        )
    return result


def _journal_location_summary(db_path: str | os.PathLike | None) -> dict[str, str]:
    from .dogfood import resolve_journal_path

    resolved = Path(resolve_journal_path(db_path=db_path))
    explicit = os.getenv("DOGFOOD_JOURNAL_PATH")
    if explicit:
        scope = "configured local path"
    elif db_path or os.getenv("SQLITE_DB_PATH"):
        scope = "next to the SQLite database"
    else:
        scope = "current working directory"
    return {"name": resolved.name, "scope": scope}


async def onboarding_status(engine) -> dict[str, Any]:
    """Compute first-run readiness from real storage and local configuration."""
    memory_count = await engine.episodic.count()
    memories = await engine.episodic.get_all(limit=max(memory_count, 1)) if memory_count else []
    demo_count = sum(1 for m in memories if bool((m.metadata or {}).get("demo")))
    receipt = read_receipt()
    mcp_client = receipt.get("mcp_client") if receipt else None
    mcp_profile = receipt.get("mcp_profile") if receipt else DEFAULT_PROFILE
    first_memory_ready = memory_count > 0
    mcp_configured = bool(mcp_client)
    ready = first_memory_ready and mcp_configured

    if memory_count == 0:
        next_step = "choose_demo_or_real_setup"
    elif not mcp_configured:
        next_step = "configure_mcp_client"
    else:
        next_step = "test_recall"

    requested_mode = getattr(engine, "_embedder_mode", os.getenv("EMBEDDER_MODE", "auto"))
    db_path = getattr(getattr(engine, "db", None), "db_path", None)
    from .dogfood import dogfood_enabled

    dogfood = dogfood_enabled()
    checks = [
        {
            "id": "database",
            "status": "pass",
            "message": "Local database is ready",
        },
        {
            "id": "memory",
            "status": "pass" if first_memory_ready else "pending",
            "message": (
                f"{memory_count} memories available"
                if first_memory_ready
                else "Store, import, or seed your first memory"
            ),
        },
        {
            "id": "mcp",
            "status": "pass" if mcp_configured else "pending",
            "message": (
                f"{mcp_client} configured with the {mcp_profile} profile"
                if mcp_configured
                else "Generate a configuration for your AI client"
            ),
        },
    ]
    return {
        "first_run": memory_count == 0,
        "ready": ready,
        "memory_count": memory_count,
        "database_initialized": True,
        "embedder_mode": requested_mode,
        "mcp_default_profile": DEFAULT_PROFILE,
        "mcp_configured": mcp_configured,
        "mcp_client": mcp_client,
        "mcp_profile": mcp_profile,
        "profile_counts": profile_counts(),
        "clients": public_client_options(),
        "profiles_are_security_boundary": False,
        "profile_warning": (
            "MCP profiles reduce the advertised tool surface; they are not an "
            "authorization or security boundary."
        ),
        "dogfood_enabled": dogfood,
        "dogfood_journal": _journal_location_summary(db_path),
        "dogfood_statement": (
            "Usage measurement is local and disabled by default. When enabled, "
            "only whitelisted aggregate events are recorded locally; raw memory "
            "and query content are not recorded, and no data is sent over the network."
        ),
        "demo_seeded": demo_count > 0,
        "demo_memory_count": demo_count,
        "recommended_next_step": next_step,
        "checks": checks,
    }


async def remove_demo_data(engine) -> dict[str, Any]:
    """Delete only memories explicitly marked ``metadata.demo=true``.

    Every deletion uses the existing purge+postcondition path. Real memories
    are untouched. Derived entity/trust/conflict state is rebuilt afterwards.
    """
    memories = await engine.episodic.get_all(limit=1_000_000)
    demo_ids = [m.id for m in memories if bool((m.metadata or {}).get("demo"))]
    audits: list[dict[str, Any]] = []
    for memory_id in demo_ids:
        audits.append(await engine.purge_memory(memory_id))

    if demo_ids:
        await engine.db.delete_conflicts_for_memory_ids(demo_ids)
        entities = await engine.reindex_entities()
        trust = await engine.recompute_trust_scores()
    else:
        entities = {"entities": 0, "links": 0}
        trust = {"scored": await engine.episodic.count()}

    remaining = await engine.episodic.count()
    return {
        "removed": len(demo_ids),
        "remaining": remaining,
        "fully_purged": all(a.get("purged") for a in audits),
        "audits": [
            {
                "memory_id": a.get("memory_id"),
                "purged": bool(a.get("purged")),
                "residue": a.get("residue", {}),
            }
            for a in audits
        ],
        "entities": entities.get("entities", 0),
        "entity_links": entities.get("links", 0),
        "trust_scored": trust.get("scored", 0),
    }
