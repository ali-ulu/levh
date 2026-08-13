"""Access-log filtering for the health-check poll.

The dashboard header polls /api/health to drive its online badge, which buries
every other access-log line under a steady drip of 200s. These tests pin the
two halves of the rule: successful health polls are dropped, and anything that
is not a successful health poll still reaches the log.
"""

import logging

from server.core.log_filters import HealthCheckAccessFilter, install_access_log_filters


def _access_record(path: str, status: int) -> logging.LogRecord:
    """Build a record shaped like uvicorn.access emits one."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:61661", "GET", path, "1.1", status),
        exc_info=None,
    )


def test_drops_successful_health_poll():
    assert HealthCheckAccessFilter().filter(_access_record("/api/health", 200)) is False


def test_keeps_failing_health_check():
    """A health check that starts failing is the one line we must not hide."""
    flt = HealthCheckAccessFilter()
    assert flt.filter(_access_record("/api/health", 500)) is True
    assert flt.filter(_access_record("/api/health", 404)) is True


def test_keeps_other_endpoints():
    flt = HealthCheckAccessFilter()
    assert flt.filter(_access_record("/api/stats", 200)) is True
    assert flt.filter(_access_record("/api/health/deep", 200)) is True


def test_ignores_query_string_on_health():
    assert (
        HealthCheckAccessFilter().filter(_access_record("/api/health?t=1", 200)) is False
    )


def test_passes_records_it_does_not_understand():
    """Never swallow a line just because it is not shaped like access output."""
    flt = HealthCheckAccessFilter()
    plain = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something else entirely",
        args=None,
        exc_info=None,
    )
    assert flt.filter(plain) is True
    short = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s",
        args=("/api/health",),
        exc_info=None,
    )
    assert flt.filter(short) is True


def test_install_is_idempotent():
    """cmd_serve may run more than once in a process (tests, --reload)."""
    logger = logging.getLogger("uvicorn.access")
    before = list(logger.filters)
    try:
        install_access_log_filters()
        install_access_log_filters()
        added = [f for f in logger.filters if isinstance(f, HealthCheckAccessFilter)]
        assert len(added) == 1
    finally:
        logger.filters = before
