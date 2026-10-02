"""Tenancy: the principal and workspace a request runs as.

Phase 1 of the design in ``docs/internal/SHARED-MEMORY-DESIGN.md`` (#302).

The local-first product is a degenerate case of a tenanted server: one implicit
workspace (``default``) and one implicit principal (``local``). Nothing about
running single-user changes — but every storage read and write is now scoped to
the current workspace, so a future server mode inherits the boundary instead of
bolting it on.

The principal travels in a :class:`~contextvars.ContextVar`, the same mechanism
``request_context`` already uses for the correlation id: the storage layer can
consult it without every query signature growing a parameter, and a transport
binds it once per request.

This module is deliberately pure (stdlib only). It is the storage layer's
dependency, so it must not import the engine or the query groups.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

#: The one workspace a single-user install has.
DEFAULT_WORKSPACE_ID = "default"

#: The principal a request runs as when no account layer is in play.
DEFAULT_PRINCIPAL_ID = "local"

#: Roles, ordered by capability. Enforced at the storage boundary (phase 2);
#: declared here so the vocabulary has one owner.
ROLES = ("viewer", "editor", "admin")


@dataclass(frozen=True)
class Principal:
    """Who a request runs as.

    ``id`` identifies the principal; ``workspace_id`` is the tenancy boundary
    its storage access is scoped to; ``role`` is the capability it holds inside
    that workspace. ``agent`` is the client/agent name (``claude-code``,
    ``cli``, …) when the transport knows one, and ``None`` otherwise.
    """

    id: str = DEFAULT_PRINCIPAL_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    role: str = "admin"
    agent: str | None = None


#: The principal used when nothing has bound one: the local single-user case.
LOCAL_PRINCIPAL = Principal()

_current: ContextVar[Principal] = ContextVar(
    "levh_principal", default=LOCAL_PRINCIPAL
)


def current_principal() -> Principal:
    """The principal bound to this task, or the local default."""
    return _current.get()


def current_workspace_id() -> str:
    """The workspace every storage access in this task is scoped to."""
    return _current.get().workspace_id


def bind_principal(principal: Principal) -> Token:
    """Bind *principal* for the current task; returns the token for reset."""
    return _current.set(principal)


def reset_principal(token: Token) -> None:
    _current.reset(token)
