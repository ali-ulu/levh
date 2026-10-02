# Shared / team memory server: tenancy, auth, storage (#302)

Tarih: 2026-10-02 · Durum: öneri — maintainer onayı bekliyor · Tür: tasarım önerisi

This is the design issue #302 asked for. It **proposes a shape; it is not a
request to start coding**. `CONTRIBUTING.md` requires a design issue before any
cloud, auth, billing, or workspace feature; no code lands until the maintainer
agrees the direction here. Per the issue's own scope note, a reasonable
conclusion is "not now, and here is what would change that" — this document
recommends exactly that for most of the layers and says which one has to be
decided first.

## What exists today (the boundary being extended)

- **One principal for the whole server.** `LEVH_TOKEN` is compared in
  `server/auth.py` / `server/middleware.py` (`RemoteAccessBoundaryMiddleware`).
  It authenticates *the server*, not a user: it identifies no workspace and no
  role. `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN` already exists as the explicit
  "operator owns the network boundary" escape hatch, and loopback is the
  default. This is the layer the issue calls "no identity to hang anything on".
- **`project` is a label, not a boundary.** `memories.project` is a TEXT column
  with an index (`server/core/db/schema.py`), used to keep unrelated projects
  from bleeding into each other in recall. It is set by the caller and never
  checked against a caller's right to that project. Promoting it to a security
  boundary without a principal would be exactly the "bolt identity onto a layer
  that has none" failure the issue warns about.
- **One engine per process.** All transports resolve the engine through
  `server/core/engine_provider.get_engine()`, and `AGENTS.md` forbids
  constructing a second `MemoryEngine` in a request path (it would split the
  short-term deque and the vector store). This is a hard constraint on the
  tenancy design: a multi-workspace server must **not** become
  one-engine-per-workspace. It has to be one engine whose store is
  workspace-scoped by the request's context.
- **A per-request context precedent already exists.**
  `server/core/request_context.py` carries a correlation id in a `ContextVar`
  so every module can read it without threading a parameter. A per-request
  principal is the same shape and should use the same mechanism.
- **The store is already an enforcement point.** The schema enforces the
  model's contract on INSERT/UPDATE through triggers generated from
  `_MEMORY_ROW_RULES`, and `row_to_memory_dict` is the single place the storage
  shape becomes the model. The store is therefore the natural place to enforce
  roles and workspace scoping — the codebase already believes in that pattern.
- **Audit substrate exists.** `recall_log` records one row per recall (query
  already redacted by the caller, ranked ids, filters). "Who read this memory,
  and when" is close to answerable once a principal exists, without storing
  memory content again.
- **A cross-instance primitive exists.** `server/core/federation.py` (#338)
  signs a `full_export` bundle into a self-describing envelope and re-enters
  through the admission gate. It is the cheap answer to *read-only* sharing.

## The line this proposal must not cross

**The local product gains no account requirement.** No-token single-user mode
stays the default and stays first-class: it is the degenerate case of exactly
one implicit workspace (`default`) with one implicit principal (`local`), not a
special path. An operator who never sets a token, never starts the account
service, and never points LEVH at an IdP must see byte-for-byte the product
that exists today. Every decision below is judged against that.

## Decision 1 — Identity and tenancy: settle the model now, implement the degenerate case

**Recommended:** adopt a `Principal` and a `Workspace` model as the foundation,
and implement it first as the degenerate single-user case. The architectural
rule that matters: **the workspace boundary is enforced at the storage
boundary**, by resolving a principal + workspace from the per-request context
and refusing reads/writes outside it — not by adding a check to each route.

- A memory row gains `workspace_id`. The local default is a single row
  (`default`); nothing changes for existing stores beyond a migration that
  backfills it.
- The principal travels the way `request_context` already travels, so the
  storage layer can consult it without every signature growing a parameter.
- The engine stays one per process; workspace scoping is a filter on every
  query group in `server/core/db/`, not a second engine.

**Why first.** The issue is right that identity must precede storage: a
Postgres backend added before the tenancy model encodes single-principal
assumptions into the schema and migrations, which is expensive to undo. This is
also the only layer that can be built with **zero** user-visible change, which
is what makes it safe to do before anyone has agreed to a hosted tier.

**The biggest fork, deliberately left open below:** one deployment serving many
workspaces, versus one workspace per deployment. Everything else is downstream
of that answer.

## Decision 2 — Storage: SQLite stays the default; Postgres is deferred behind a trigger

**Recommended:** SQLite remains the default *and* the recommendation. A
PostgreSQL backend (with pgvector) is a **later, opt-in** backend, not part of
the first phase, and the wheel must never depend on a Postgres driver.

- Shared concurrent writers is a real weakness of SQLite, but it is a weakness
  at *multiple writer processes across hosts*. A small team on one host is
  served by SQLite in WAL mode today.
- **Trigger for revisiting:** a deployment needs more than one writer process
  on more than one host, *and* the tenancy model (Decision 1) is implemented
  and tested on SQLite. Until then Postgres buys nothing but a second test
  matrix.
- **Thesis-risk mitigation, concretely:** the backend is selected by an
  explicit setting, the default code path never imports a Postgres module, and
  the dependency lives in an extra so `pip install levh` is unchanged.

This is the largest single piece of work in the issue and the one the issue
itself flags as most arguable. The recommendation is to *not* do it yet.

## Decision 3 — Roles: viewer / editor / admin, enforced where the store already enforces

**Recommended:** three roles, enforced at the storage boundary through one
`authorize(principal, action, workspace)` consulted by the query layer — the
same shape the schema already uses for row rules — so a new route cannot
forget a check.

- `viewer` — read, recall, read-only export.
- `editor` — store, update, forget, admit.
- `admin` — membership, server configuration, full export, backup/restore.

Role checks live with the data, not in route handlers. A route that is added
later inherits the boundary instead of having to remember it.

## Decision 4 — Auth: OIDC first, and only in server mode

**Recommended:** OIDC first (one integration covers Okta / Entra / Keycloak /
Google); SAML is a follow-on, not phase one.

Two constraints that follow from the local-first thesis:

- **The IdP is only reachable in server mode.** Local mode must never require
  it, and the account service must be optional at startup.
- **`LEVH_TOKEN` survives as a machine principal.** The CLI, the MCP stdio/SSE
  transports and the connectors authenticate as a principal of type `agent`,
  not as a human user. Deleting the token in favour of users would break every
  non-browser client; the token becomes the agent's credential inside a
  workspace rather than the server's single identity.

## Decision 5 — Billing: out of scope, separate service

Noted only so it is not accidentally designed into the engine: billing must be
a separate service and never a dependency of the engine. The engine's sole
obligation to it is stable ids on principals and workspaces.

## Recommended sequencing

| Phase | Content | User-visible change |
| --- | --- | --- |
| 0 | Agree the shape in this document (this PR) | none |
| 1 | Tenancy as the degenerate case on SQLite: `workspace_id`, principal context, storage-boundary scoping | none — one implicit workspace |
| 2 | Roles + an access-audit query surface ("who read this memory, and when") on top of `recall_log` | none in local mode |
| 3 | OIDC, server mode only | opt-in |
| 4 | Postgres backend, only if the trigger in Decision 2 fires | opt-in |

Phases 1–2 are the ones that are cheap, invisible to local users, and
irreversible-if-skipped. Phases 3–4 are the ones with real surface area and
should wait for an actual deployment that needs them.

## What would change this answer

- **Nobody runs a shared server.** Then do nothing; stay strictly single-user.
  This is the issue's own first alternative and a fine outcome.
- **The need is read-only sharing.** The signed federation envelope (#338) is a
  much cheaper answer than tenancy: exchange an envelope between instances
  instead of building accounts. Prefer this until someone needs *concurrent
  write* sharing.
- **A regulated buyer requires Postgres specifically.** Phase 4 moves up, but
  still after phase 1 — the schema must not encode single-principal
  assumptions first.

## Alternatives and why they are not recommended now

- **Do nothing; stay strictly single-user.** Recommended *until* one of the
  triggers above fires. It keeps the thesis clean and the surface small.
- **Hosted-only cloud, never on-prem.** Cheaper to build, but it contradicts
  "memory does not leave the machine" and forfeits the on-prem segment the
  issue identifies as the highest-ARPU one.
- **Per-user SQLite files with a sync layer, no tenancy model.** Avoids a
  server-side identity model but does not produce a *shared* memory, which is
  the actual request. (The federation envelope covers the read-only half of
  this without pretending to be sharing.)

## Open questions for the maintainer

1. **One deployment, many workspaces, or one workspace per deployment?** The
   biggest fork; it decides whether the boundary is a column or a process.
2. **Is `project` promoted to `workspace_id`, or kept as a label inside a
   workspace?** Recommendation: kept as a label; workspace is the security
   boundary and `project` stays a recall filter.
3. **Does an agent principal belong to a user or to the workspace?** A shared
   CI agent should probably belong to the workspace; a developer's local agent
   to that developer.
4. **Is phase 1 (the degenerate case) worth doing before any user has asked for
   a shared server?** Recommendation: only if the maintainer expects the
   trigger to fire; otherwise this stays a written decision and no code lands.

## Surface

Storage / engine internals now; REST API, CLI and Dashboard only in phases 2–4.
This document touches none of them.
