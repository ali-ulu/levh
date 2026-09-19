"""Per-request correlation id (issue #145).

A multi-step recall → admission → store flow spans several modules, and each
logged a sentence of its own with nothing tying the lines together. In text
logs that is merely inconvenient; in JSON logs it means the records are
unjoinable, which is the point of emitting them at all.

The id travels in a :class:`~contextvars.ContextVar` rather than in function
arguments: every module can read it without threading a parameter through
calls whose signatures have nothing to do with logging. The middleware sets it
once per request, event-loop tasks inherit it, and ``log_event`` picks it up.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

#: The header a caller may set to supply its own correlation id.
REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("levh_request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> object:
    """Bind *value* for the current task; returns the token for :func:`reset`."""
    return _request_id.set(value)


def reset_request_id(token: object) -> None:
    _request_id.reset(token)  # type: ignore[arg-type]