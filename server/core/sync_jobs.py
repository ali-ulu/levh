"""Background connector-sync jobs — the queue behind slow syncs.

A synchronous ``POST /api/connectors/sync`` holds the HTTP connection until
fetch + gate + store all finish. On a shared server that can outlast the
client's patience while the work itself completes (the sync-state row is
written, the client just never sees the response). Submitting with
``background=true`` returns ``202`` immediately with a job id; the same
pipeline then runs in an asyncio task and the result (including the per-stage
``timing_ms`` from the #390 instrumentation) is polled via
``GET /api/connectors/sync-jobs/{job_id}``.

Best-effort by design: jobs live in memory. A restart loses the job records
(the stored memories and the ``connector_sync`` row do not — those are in
SQLite, so re-submitting after a restart dedupes instead of duplicating).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any


class SyncJobRunner:
    """Run connector syncs as tracked asyncio tasks."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def list_jobs(self) -> list[dict[str, Any]]:
        """Newest first. Returns copies — callers cannot mutate the registry."""
        return sorted(
            (dict(job) for job in self._jobs.values()),
            key=lambda j: j.get("created_at", ""),
            reverse=True,
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        return dict(job) if job is not None else None

    async def submit(
        self,
        engine: Any,
        connector: str,
        config: dict,
        project: str | None = None,
        use_gate: bool = True,
    ) -> dict[str, Any]:
        """Queue a sync and return its job record (status ``pending``)."""
        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "pending",
                "connector": connector,
                "project": project,
                "use_gate": use_gate,
                "created_at": now,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._run(job_id, engine, connector, config, project, use_gate)
            )
            return dict(self._jobs[job_id])

    async def _run(
        self,
        job_id: str,
        engine: Any,
        connector: str,
        config: dict,
        project: str | None,
        use_gate: bool,
    ) -> None:
        from server.connectors import get_connector

        started = time.perf_counter()
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
        try:
            conn = get_connector(connector)
        except KeyError as exc:
            await self._finish(job_id, error=str(exc))
            return
        try:
            await conn.connect(config)
        except (FileNotFoundError, ValueError, ConnectionError) as exc:
            await self._finish(job_id, error=f"Connection failed: {exc}")
            return
        fetch_start = time.perf_counter()
        try:
            items = await conn.fetch()
        except Exception:  # noqa: BLE001 - the job record carries the failure as text
            await conn.disconnect()
            await self._finish(job_id, error=f"Fetch from '{connector}' failed.")
            return
        fetch_ms = (time.perf_counter() - fetch_start) * 1000.0
        try:
            result = await engine.ingest_items(
                items, connector=connector, project=project, use_gate=use_gate
            )
        finally:
            await conn.disconnect()
        timing = dict(result.get("timing_ms") or {})
        timing["fetch"] = round(fetch_ms, 1)
        timing["job_total"] = round((time.perf_counter() - started) * 1000.0, 1)
        result["timing_ms"] = timing
        await self._finish(job_id, result=result)

    async def _finish(
        self,
        job_id: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "error" if error else "done"
            job["result"] = result
            job["error"] = error
            job["finished_at"] = datetime.now(timezone.utc).isoformat()


# One runner per process: jobs are process-local by design (see module
# docstring), so a singleton keeps the route, the MCP surface and the
# auto-feed loop on the same registry.
_runner: SyncJobRunner | None = None


def get_runner() -> SyncJobRunner:
    """The process-wide job runner, created on first use."""
    global _runner
    if _runner is None:
        _runner = SyncJobRunner()
    return _runner


def reset_runner() -> None:
    """Forget all jobs. Tests only — production never calls this."""
    global _runner
    _runner = None
