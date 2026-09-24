"""Structured logging for the server (issue #145).

The logs were human sentences: ``logger.info("Reindexed %d memories", n)``. That
is pleasant to read and impossible to query — a soak test or a support bundle
gives you a wall of text with no stable fields, so "how many retries did the
retry layer do today" is a ``grep -c`` over prose that happens to still match.

This module keeps the prose (a JSON line still carries ``event`` and
``message``) but adds a machine-readable payload beside it. :func:`emit` writes
one JSON object straight to ``stdout`` — used by anything that runs before
logging is configured, so the record cannot vanish while handlers are still
missing. Once logging is configured, :class:`JsonFormatter` renders every
record on the standard library logging pipeline as that same object, so
``LEVH_LOG_JSON=1`` makes the whole server emit structured records with no
formatter registry to maintain.

The knob is the environment, not a new config file: ``LEVH_LOG_JSON=1``.
Unset means the previous human formatter, so existing operators see no change.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

#: Set to ``1`` (or ``true``/``yes``) to make every log record a JSON line.
LOG_JSON_ENV = "LEVH_LOG_JSON"


def json_enabled() -> bool:
    return os.environ.get(LOG_JSON_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _record(
    event: str,
    level: str,
    fields: dict[str, object],
    message: str | None,
) -> dict[str, object]:
    from server.core.request_context import current_request_id

    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
    }
    request_id = current_request_id()
    if request_id:
        record["request_id"] = request_id
    if message is not None:
        record["message"] = message
    # Caller fields come last so a caller cannot silently shadow the envelope
    # (``event``/``ts``/``level``) and make a record unparseable.
    for key, value in fields.items():
        if key not in record:
            record[key] = value
    return record


def emit(
    _logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    message: str | None = None,
    stream: object = None,
    **fields: object,
) -> None:
    """Emit *event*, bypassing the configured handlers entirely.

    Prefer :func:`install_logging` plus a normal ``logger.info`` call: once the
    root logger has handlers, :class:`JsonFormatter` structures the record on
    the standard pipeline. This helper exists for output that is written before
    that setup can run, where a record would vanish when JSON mode is on. It
    writes the JSON line to *stream* (``sys.stdout`` by default) and does
    nothing else.
    """
    if not json_enabled():
        if message:
            print(message, file=stream or sys.stdout)
        return
    payload = _record(event, logging.getLevelName(level), fields, message)
    print(json.dumps(payload, ensure_ascii=False, default=str), file=stream or sys.stdout)


class JsonFormatter(logging.Formatter):
    """Render a log record as one JSON line.

    A record that carries a ``structured`` object (set via ``extra=``) is
    rendered exactly. A record from anywhere else still gets a JSON line: ``message`` is the
    formatted text and ``event`` falls back to the caller's function name, so
    third-party loggers (uvicorn, httpx) are structured too rather than mixed
    in as bare text.
    """

    def format(self, record: logging.LogRecord) -> str:
        structured = getattr(record, "structured", None)
        if structured is None:
            structured = {
                "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "event": record.funcName,
                "message": record.getMessage(),
            }
        # Tracebacks are multi-line text; keep them as a field rather than
        # letting them break the one-object-per-line contract.
        if record.exc_info:
            structured = {**structured, "exc_info": self.formatException(record.exc_info)}
        return json.dumps(structured, ensure_ascii=False, default=str)

def install_logging() -> None:
    """Route the root logger through :class:`JsonFormatter` when JSON is on.

    Called once at startup. Reconfigures existing handlers (uvicorn installs
    its own) instead of adding a second one, so a record is emitted once and
    third-party loggers get structured output too. A no-op when
    ``LEVH_LOG_JSON`` is unset, leaving the previous formatter in place.
    """
    if not json_enabled():
        return
    formatter = JsonFormatter()
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
