# Internal roadmap: report items and deferred work

Tarih: 2026-09-27 · Kapsam: dış rapor maddeleri · Tür: karar kaydı

This is the decision record for the items in the external quality/feature report
that are **not** being implemented now. It exists so that a decision — "skipped",
"cancelled", "deferred", "proposed" — is a fact in the tree instead of something
that has to be reconstructed from a conversation or an issue thread.

It is internal on purpose: a dated list of open gaps reads as a product claim
when it reaches `docs/*.md`, which is why it lives here alongside the other
maintainer inventories.

`tests/test_roadmap.py` parses the tables below. Every row must carry one of the
allowed states, and an *open* row (`deferred`, `proposed`) must name a next step:
an open item that names nothing actionable is indistinguishable from one that was
quietly dropped. Adding a row without a next step is what that test refuses.

## Report items

| Item | Topic | State | Next step | Reference |
| --- | --- | --- | --- | --- |
| 4 | Frontend i18n (more than one UI language) | in-progress | Extraction mechanism settled in #308 (key-addressed catalogue + `useT()`, no runtime library; see `docs/internal/I18N-DESIGN.md`). The ratchet-driven rollout is active and has progressed through the Ask Your Memory conversion (#418); continue one surface per PR, lowering or removing that surface's `frontend/scripts/ui-string-baseline.json` entry while leaving technical false positives pinned explicitly. | #308, #418 |
| 5 | End-to-end tests over a real server and browser | done | — | #299 |
| 6 | Page-level accessibility coverage (axe over each route) | done | — | #299 |
| 8 | Not recorded at decision time | skipped | — | |
| 9 | Not recorded at decision time | cancelled | — | |
| 10 | Not recorded at decision time | ignored | — | |
| 11 | Not recorded at decision time | skipped | — | |
| B | Commercial surfaces and a shared/team memory server | proposed | The decision rule #298 asked for is recorded in [`COMMERCIAL-SURFACES-DESIGN.md`](COMMERCIAL-SURFACES-DESIGN.md): adopt support/SLA/signed build now, approve single-user hosted sync in principle, and route the team workspace and SSO/metering to the phase 2–4 triggers in [`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md) (#302, phase 1 landed). Concrete feature lists per revenue path are in #309. No funded surface is implemented yet; the next step is whichever one the owner funds. | #298, #302, #309 |

## Backlog workstreams

These are the deferred workstreams named alongside the report items. They are
grouped separately because they are recurring product surfaces rather than
numbered findings, and because each one is also a candidate revenue surface.

| Item | Topic | State | Next step | Reference |
| --- | --- | --- | --- | --- |
| connectors | Slack sync connector | done | — | #421 |
| continuity-proof | Measure that the continuity brief is emitted and used, not just built to be emitted | in-progress | A1 measurement is landed (#378): `continuity_log`, config counters, and the golden continuity-use fixture measure emission/use. A2 is also landed (#423): stdio and SSE publish the shared `get_continuity_brief` start directive through MCP server instructions, with black-box protocol coverage. Remaining code/documentation work is A0: reconcile the client × transport inventory so stderr / native hook / MCP tool defaults cannot drift between docs and installer registries. | #378, #423 |
| memory-federation | Peer memory exchange with provenance, signature and receiver-side decay | proposed | B0 is landed: the signed offline federation envelope and CLI verify peer provenance before re-entry through `import_memories_gated` (#356, #357 / #338). Remaining B1 is a pull-first exchange transport, which stays blocked on the tenancy decision because identity determines which peer may pull from which workspace. | #338, #356, #357, #302 |
| typescript-sdk | TypeScript SDK over the REST and MCP surface | done | — | #307 |
| git-enrichment | Make the git and GitHub connectors actually feed memory | done | — | #374, #399 |
| windowing-graph | Bind the entity graph and an adaptive budget into the context window | done | — | #375 |
| recall-log-coverage | Populate `recall_log` and expose recall access audit | done | — | #376, #382, #395 |
| multi-user-postgres | Multi-user auth, tenancy, and a Postgres backend | deferred | The tenancy shape is proposed in [`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md) (#302): implement tenancy as the degenerate single-user case on SQLite first, roles at the storage boundary second, OIDC only in server mode third, and Postgres last — behind the explicit trigger in that document. Blocked on the maintainer accepting the proposal and answering its four open questions; identity must be designed before storage or the schema encodes single-principal assumptions. | #302 |

## Why these are deferred rather than started

The remaining architecture-heavy deferred/proposed items are gated on one of two design
decisions that are open on purpose. The active i18n rollout, continuity proof,
and Slack connector do not need either gate:

- **Tenancy.** `LEVH_TOKEN` gates the whole server as a single principal — it
  identifies no user, workspace, or role. Team features and a hosted tier both
  build on that boundary, so starting either before it is settled would bolt
  identity onto a layer that has none. See #302.
- **Revenue.** Nothing in the tree can be charged for today, and the commercial
  surfaces (support/SLA, hosted sync, team workspace, SSO + metering) are ordered
  by how little they disturb the local-first core. See #298.

The Slack connector, frontend i18n rollout, and continuity proof are the remaining
workstreams that do **not** need either decision first, which is why they are the
cheapest to keep moving. Jira and Linear landed on the same reasoning: they cost
no new architecture, and the sync framework they plug into already existed. The
TypeScript SDK is already done because the contract it generates from was frozen.
