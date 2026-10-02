# Shared / team memory server: tenancy, auth, storage (#302)

Tarih: 2026-10-02 · Durum: kararlar alındı, faz 1 uygulandı · Tür: tasarım + karar kaydı

This is the design issue #302 asked for. It **proposes a shape**. The four
questions it left open were delegated back to the agent and are now answered in
"Open questions for the maintainer — answered" below; phase 1 of the recommended
sequencing is implemented, and the rest stays a written decision until its
trigger fires.

`CONTRIBUTING.md` requires a design issue before any cloud, auth, billing, or
workspace feature; no code lands until the maintainer agrees the direction here.
Per the issue's own scope note, a reasonable conclusion is "not now, and here
is what would change that" — this document recommends exactly that for most of
the layers and says which one has to be decided first.

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

## Open questions for the maintainer — answered

These four were delegated to the agent; the answers below are now decisions and
phase 1 is implemented against them.

1. **One deployment, many workspaces.** The boundary is a column
   (`memories.workspace_id`), not a process. This follows from the hard
   constraint already in `AGENTS.md`: all transports share one
   `MemoryEngine`, so one-workspace-per-deployment would have to become
   one-engine-per-workspace, which the codebase forbids. A per-process boundary
   would also make "add a workspace" a restart rather than a row. The local
   product is the degenerate case: exactly one workspace, `default`.
2. **`project` stays a label inside a workspace.** It is caller-supplied and
   never checked against a right, so promoting it to the boundary would be the
   "bolt identity onto a layer that has none" failure the issue warns about.
   `workspace_id` is the security boundary; `project` remains a recall filter
   *within* it.
3. **An agent principal belongs to the workspace, not to a user.** The
   non-browser clients — CLI, MCP stdio/SSE, connectors — authenticate as a
   principal of type `agent` whose `workspace_id` is the workspace it was
   configured for. A shared CI agent has no human owner to hang off, and tying
   it to a person would break the moment that person leaves. A developer's
   local agent is the degenerate case: workspace `default`, principal `local`.
4. **Phase 1 is worth doing now.** *Yes*, because phase 1 is the only layer
   that is invisible to local users and irreversible-if-skipped: once a
   Postgres backend or a role system exists, a schema without `workspace_id`
   encodes single-principal assumptions that are expensive to undo. Phases 2–4
   still wait for their triggers. The counter-argument — no user has asked for
   a shared server yet — is real, which is why nothing beyond the degenerate
   case landed: no accounts, no roles enforced, no OIDC, no second backend.

### Phase 1 as implemented

- `server/core/tenancy.py` — `Principal`, `DEFAULT_WORKSPACE_ID`, and
  `current_workspace_id()` over a `ContextVar`, the same mechanism
  `request_context` already uses. Stdlib only: it is the storage layer's
  dependency and must not import the engine.
- `memories.workspace_id` (schema v4, additive, default `'default'`), indexed
  because every read is scoped. A v3 store is migrated and every existing row
  is backfilled into `default`; nothing is dropped.
- Every read/write in `server/core/db/` resolves its workspace from the context
  per call — not captured at construction, since one engine serves every
  request in the process. The `COALESCE(workspace_id, 'default')` guard makes a
  pre-backfill NULL read as the default workspace rather than vanish.
- The in-process vector store and short-term deque mirror the *whole* store
  (the alternative is a partial mirror that silently makes a row unrecallable);
  separation for recall is enforced in the candidate predicate, the only place
  a read from that mirror can be filtered.
- `insert_memory` stamps the context's workspace and ignores any
  `workspace_id` in the caller's payload, so a request cannot choose its own
  boundary.
- `request_id_middleware` (in `server/middleware.py`, beside the existing
  correlation-id binding) binds the local principal per request, so the
  transport that will later identify a user already has the seam.
- Store-wide maintenance passes (re-embed, demo purge) keep their whole-store
  scan but bind each row's own workspace around the write, because the scan
  spans workspaces while `update_memory`/`purge_memory` are scoped. A replace
  restore has no workspace dimension to scope to (sessions carry no owner until
  phase 2), so it refuses outright once a second workspace exists rather than
  delete a peer's sessions and derived state.

Tests: `tests/test_workspace_tenancy.py` pins the single-user round trip
unchanged, the cross-workspace invisibility of reads/counts/updates/deletes,
the recall filter over the shared mirror, and the v3→v4 migration.

## Surface

Storage / engine internals now; REST API, CLI and Dashboard only in phases 2–4.
This document touches none of them.
