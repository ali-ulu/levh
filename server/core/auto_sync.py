"""Auto-feed: run connector syncs on a timer while the server lives.

Pull-on-demand was the recorded design (ROADMAP): connectors only run when
someone calls them. Owner decision 2026-10-04 (#374): automatic feeding is
wanted too. This module is the opt-in half — disabled unless the local config
file says otherwise, so a default install behaves exactly as before.

Config lives in ``.stackmemory/config.json`` next to the other runtime keys::

    {
      "auto_sync": {
        "enabled": true,
        "interval_seconds": 3600,
        "jobs": [
          {"connector": "git",
           "config": {"repo_path": "/path/to/repo", "max_commits": 50,
                      "include_file_history": true, "include_snapshot": true},
           "project": "levh"},
          {"connector": "github",
           "config": {"token": "env:GITHUB_TOKEN", "repos": ["owner/repo"]},
           "project": "levh"}
        ]
      }
    }

Secrets never go in the file: a config value of the form ``"env:NAME"`` is
resolved from the process environment at run time (``"token": "env:GITHUB_TOKEN"``
reads ``GITHUB_TOKEN``). A missing variable fails that job with a clear error
instead of syncing anonymously.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("levh.auto_sync")

DEFAULT_INTERVAL_SECONDS = 3600


def resolve_env_refs(value: Any) -> Any:
    """Replace ``"env:NAME"`` strings with the environment value.

    Walks nested dicts/lists. An unset variable raises KeyError naming the
    variable — the job fails loudly instead of running half-configured.
    """
    if isinstance(value, str) and value.startswith("env:"):
        name = value[len("env:") :].strip()
        if not name:
            raise KeyError("empty env: reference")
        try:
            return os.environ[name]
        except KeyError:
            raise KeyError(f"environment variable {name} is not set") from None
    if isinstance(value, dict):
        return {key: resolve_env_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_env_refs(item) for item in value]
    return value


def load_auto_sync_jobs(config: dict | None) -> tuple[bool, int, list[dict]]:
    """Read the ``auto_sync`` section. Never raises on a missing section.

    Returns ``(enabled, interval_seconds, jobs)``. A present-but-malformed
    section raises ValueError — a scheduler that silently ignores its own
    config would be worse than one that refuses to start.
    """
    section = (config or {}).get("auto_sync") or {}
    if not isinstance(section, dict):
        raise ValueError("auto_sync must be an object")
    enabled = bool(section.get("enabled", False))
    try:
        interval = int(section.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        raise ValueError("auto_sync.interval_seconds must be an integer") from None
    if interval <= 0:
        raise ValueError("auto_sync.interval_seconds must be positive")
    raw_jobs = section.get("jobs", [])
    if not isinstance(raw_jobs, list):
        raise ValueError("auto_sync.jobs must be a list")
    jobs: list[dict] = []
    for entry in raw_jobs:
        if not isinstance(entry, dict) or not entry.get("connector"):
            raise ValueError(
                "auto_sync.jobs entries need at least a connector name"
            )
        jobs.append(
            {
                "connector": str(entry["connector"]),
                "config": entry.get("config") or {},
                "project": entry.get("project"),
                "use_gate": bool(entry.get("use_gate", True)),
            }
        )
    return enabled, interval, jobs


async def run_jobs_once(engine: Any, jobs: list[dict]) -> list[dict]:
    """Run every job synchronously and return their ingest reports.

    Sequential, not parallel: the engine owns a single SQLite connection, so
    parallel syncs would only queue behind each other while hiding the order.
    One bad job never stops the rest.
    """
    from server.connectors import get_connector

    reports: list[dict] = []
    for job in jobs:
        connector = job["connector"]
        try:
            config = resolve_env_refs(job.get("config") or {})
        except KeyError as exc:
            logger.warning("auto_sync: skipping '%s': %s", connector, exc)
            reports.append({"connector": connector, "error": str(exc)})
            continue
        try:
            conn = get_connector(connector)
        except KeyError as exc:
            logger.warning("auto_sync: unknown connector '%s': %s", connector, exc)
            reports.append({"connector": connector, "error": str(exc)})
            continue
        try:
            await conn.connect(config)
        except (FileNotFoundError, ValueError, ConnectionError) as exc:
            logger.warning("auto_sync: '%s' connect failed: %s", connector, exc)
            reports.append({"connector": connector, "error": str(exc)})
            continue
        try:
            items = await conn.fetch()
        except Exception:
            # Logged here; the loop continues with the next job.
            logger.exception("auto_sync: '%s' fetch failed", connector)
            await conn.disconnect()
            reports.append({"connector": connector, "error": "fetch failed"})
            continue
        try:
            result = await engine.ingest_items(
                items,
                connector=connector,
                project=job.get("project"),
                use_gate=job.get("use_gate", True),
            )
        finally:
            await conn.disconnect()
        logger.info(
            "auto_sync: '%s' fetched=%s stored=%s",
            connector,
            result.get("fetched"),
            result.get("stored"),
        )
        reports.append(result)
    return reports


async def _loop(engine: Any, interval: int, jobs: list[dict]) -> None:
    while True:
        try:
            await run_jobs_once(engine, jobs)
        except Exception:
            # Logged; the loop outlives any single tick.
            logger.exception("auto_sync: tick failed, retrying in %ss", interval)
        await asyncio.sleep(interval)


def start_background(engine: Any) -> asyncio.Task | None:
    """Start the auto-feed loop when the config enables it, else None.

    Mirrors ``librarian.start_background``: the lifespan owns the task and
    cancels it on shutdown.
    """
    from server.core.runtime_config import load_config_file

    file_cfg, _ = load_config_file()
    enabled, interval, jobs = load_auto_sync_jobs(file_cfg)
    if not enabled or not jobs:
        return None
    logger.info(
        "auto_sync: enabled, %d job(s) every %ss", len(jobs), interval
    )
    return asyncio.get_running_loop().create_task(_loop(engine, interval, jobs))
