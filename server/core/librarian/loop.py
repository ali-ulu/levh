"""Background scan loop.

Split out of the single-file librarian (issue #98): the periodic scan +
record cycle the API lifespan starts.
"""

from __future__ import annotations

import asyncio
import logging
import os

from server.core.librarian.activity import scan
from server.core.librarian.findings import record_findings

logger = logging.getLogger("levh.librarian")

DEFAULT_INTERVAL = 600  # 10 dk


async def run_loop(interval: int = DEFAULT_INTERVAL) -> None:
    logger.info("Librarian loop started (interval=%ss)", interval)
    while True:
        try:
            report = await asyncio.to_thread(scan)
            await record_findings(report)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("librarian scan failed")
        await asyncio.sleep(interval)


def start_background() -> asyncio.Task:
    interval = int(os.getenv("LEVH_LIBRARIAN_INTERVAL", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)
    return asyncio.get_running_loop().create_task(run_loop(interval))
