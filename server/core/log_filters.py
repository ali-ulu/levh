"""Logging filters for the uvicorn access log.

Kept out of ``server/api.py`` on purpose: installing these mutates global
logging state, which belongs to whoever owns the process (``levh serve``), not
to importing the app. Tests import the app freely and must not inherit it.
"""

from __future__ import annotations

import logging

#: The published probe path plus the conventional alias it answers under
#: (#520). Monitors poll both on timers, so *successful* polls of either are
#: dropped by exact path — the alias included, or fixing the 404 storm just
#: replaces it with a 200 storm.
HEALTH_PATHS = frozenset({"/api/health", "/health"})

# uvicorn.access logs with args = (client_addr, method, path, http_version, status)
_ACCESS_ARG_COUNT = 5
_PATH_INDEX = 2
_STATUS_INDEX = 4


class HealthCheckAccessFilter(logging.Filter):
    """Drop access-log lines for *successful* health polls.

    The dashboard header polls ``/api/health`` on a timer to drive its online
    badge, and monitors reach for the ``/health`` alias, so at any real
    observation window the access log is mostly those two paths repeating.
    Only 2xx/3xx responses are dropped — a health check that starts failing
    is exactly the line worth seeing, and hiding it would trade log noise
    for a blind spot.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < _ACCESS_ARG_COUNT:
            return True
        path = str(args[_PATH_INDEX]).split("?", 1)[0]
        if path not in HEALTH_PATHS:
            return True
        try:
            status = int(args[_STATUS_INDEX])
        except (TypeError, ValueError):
            return True
        return status >= 400


def install_access_log_filters() -> None:
    """Attach the access-log filters. Safe to call more than once."""
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(f, HealthCheckAccessFilter) for f in logger.filters):
        return
    logger.addFilter(HealthCheckAccessFilter())
