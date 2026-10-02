# Internal documents

Engineering inventories that describe this repository to its maintainers, not to
its users. They are not part of the published documentation surface: internal debt
scorecards age quickly and read as product claims when seen from outside.

- [`ROADMAP.md`](ROADMAP.md) — decision record for the report items and deferred
  workstreams: each row carries a state (`deferred`, `proposed`, `done`,
  `skipped`, `cancelled`, `ignored`) and a next step. Maintained, and checked by
  `tests/test_roadmap.py`.
- [`PLAN-CONTINUITY-AND-FEDERATION.md`](PLAN-CONTINUITY-AND-FEDERATION.md) —
  proposal for the two next workstreams: measure that the continuity brief is
  emitted and used — emission being a producer-side proxy, since whether the
  agent read it is the client's behavior (Phase A) — then exchange memories
  between instances with provenance, signature and receiver-side decay (Phase
  B). A plan, not a decision record; the states live in `ROADMAP.md`.
- [`SOLID_KARNESI.md`](SOLID_KARNESI.md) — module-by-module SOLID scorecard and
  technical-debt inventory (Turkish). **Archived**: it is a 2026-09-13 snapshot
  and is no longer maintained; completed items are struck through and the file
  says so at the top. Read it for history, not for the current debt state.
- [`I18N-DESIGN.md`](I18N-DESIGN.md) — the settled frontend i18n decisions for
  #308: the extraction mechanism (a key-addressed catalogue behind `useT()`, no
  runtime library), where strings live, how the locale is stored, why the static
  export is unchanged, and the ratchet that stops a hardcoded string from
  slipping back in.
- [`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md) — the proposal #302 asked
  for: a tenancy model as the degenerate single-user case, roles enforced at the
  storage boundary, OIDC only in server mode, and Postgres deferred behind an
  explicit trigger. A proposal for the maintainer to accept or reject, not a
  decision record; the states live in `ROADMAP.md`.
