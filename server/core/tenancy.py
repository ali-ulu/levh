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

ROLE_ACTIONS = {
    "viewer": frozenset({"read", "recall", "export_readonly"}),
    "editor": frozenset(
        {
            "read",
            "recall",
            "export_readonly",
            "store",
            "update",
            "forget",
            "admit",
        }
    ),
    "admin": frozenset(
        {
            "read",
            "recall",
            "export_readonly",
            "store",
            "update",
            "forget",
            "admit",
            "membership",
            "configure",
            "export_full",
            "backup_restore",
        }
    ),
}


class AuthorizationError(PermissionError):
    """A principal attempted an action its workspace role does not allow."""


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


def authorize(
    action: str,
    workspace_id: str | None = None,
    principal: Principal | None = None,
) -> Principal:
    """Require *principal* to hold *action* inside *workspace_id*.

    The current request principal is used by default.  Workspace mismatch is
    rejected before role evaluation so a caller cannot combine a valid role
    from one workspace with a target row from another one.
    """
    actor = principal or current_principal()
    target_workspace = workspace_id or actor.workspace_id
    if actor.workspace_id != target_workspace:
        raise AuthorizationError(
            f"principal {actor.id!r} belongs to workspace {actor.workspace_id!r}, "
            f"not {target_workspace!r}"
        )
    allowed = ROLE_ACTIONS.get(actor.role)
    if allowed is None:
        raise AuthorizationError(f"unknown workspace role: {actor.role!r}")
    if action not in allowed:
        raise AuthorizationError(
            f"role {actor.role!r} is not allowed to perform {action!r}"
        )
    return actor
