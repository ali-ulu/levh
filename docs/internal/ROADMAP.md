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
| 4 | Frontend i18n (more than one UI language) | in-progress | Extraction mechanism settled in #308 (key-addressed catalogue + `useT()`, no runtime library; see `docs/internal/I18N-DESIGN.md`), with one page and the dialog primitive converted and a ratchet gate in `npm test`. Remaining: convert the ~20 pages one PR at a time, each lowering its own `frontend/scripts/ui-string-baseline.json` entry. | #308 |
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
| connectors | Slack sync connector | deferred | Jira, Linear and GitHub connectors now exist, and the pull-on-demand question is settled by the existing `ingest_items` sync framework — no worker is planned. Slack is the remaining one, and it needs OAuth-style bot credentials plus a channel-history paging decision before it is written. | #298 |
| continuity-proof | Measure that the continuity brief is emitted and used, not just built to be emitted | proposed | Phase A of [`PLAN-CONTINUITY-AND-FEDERATION.md`](PLAN-CONTINUITY-AND-FEDERATION.md): add a continuity scenario to the golden-fixture evaluator plus emission/use counters, then decide the no-hook client story from that data. Not gated on tenancy or revenue — it instruments the single-process core and can start now. | — |
| memory-federation | Peer memory exchange with provenance, signature and receiver-side decay | proposed | Phase B of [`PLAN-CONTINUITY-AND-FEDERATION.md`](PLAN-CONTINUITY-AND-FEDERATION.md): spec an offline envelope that re-enters through `import_memories_gated` before any transport; blocked on the tenancy decision. | #302, #309 |
| typescript-sdk | TypeScript SDK over the REST and MCP surface | done | — | #307 |
| git-enrichment | Make the git and GitHub connectors actually feed memory | proposed | The `git` connector is written, registered, and visible on `GET /api/v1/connectors`, and the `github` one has existed for longer, but neither has ever run: `connector_sync` holds only `transcript` and `local_files` rows. Running them through `/api/connectors/sync` is the whole remaining step for "commit history feeds memory", and it is what brings the trust and conflict layers to bear on commits. Then `blame` and live-architecture extraction, which do not exist. State and evidence: [`CROSS-AGENT-GAPS.md`](CROSS-AGENT-GAPS.md). | — |
| windowing-graph | Bind the entity graph and an adaptive budget into the context window | proposed | `get_context(query=...)` ranks with `H(x,psi)` and packs into a real token budget (#370, #371), but `_rank_context_candidates` does not use the entity graph that `recall` already treats as a candidate source, and the budget is still supplied by the caller rather than adapting to window pressure. Those two are the remainder of "the knowledge graph prepares the optimal context package". State and evidence: [`CROSS-AGENT-GAPS.md`](CROSS-AGENT-GAPS.md). | — |
| openapi-export-tree | `scripts/export_openapi.py` measures the installed package, not the checkout | proposed | Run as a file it imports the installed `levh` package, so `--check` reports "up to date" against a different tree than the one being edited: it returned a false green while a real schema change was pending, and `openapi.json` had to be regenerated by hand. Resolve the module against the script's own repository root. `tests/test_openapi_contract.py` is the authority that caught it. State and evidence: [`CROSS-AGENT-GAPS.md`](CROSS-AGENT-GAPS.md). | — |
| recall-log-coverage | `recall_log` is defined but never populated | proposed | The table exists, is documented as the audit substrate ("who read this memory, and when"), and holds zero rows on a live store. Phase 2 of [`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md) builds the access-audit surface on top of it, so an unpopulated log is the prerequisite that is silently missing. Decide the default (`LEVH_RECALL_LOG`) and cover it with a test that asserts rows appear. State and evidence: [`CROSS-AGENT-GAPS.md`](CROSS-AGENT-GAPS.md). | — |
| multi-user-postgres | Multi-user auth, tenancy, and a Postgres backend | deferred | The tenancy shape is proposed in [`SHARED-MEMORY-DESIGN.md`](SHARED-MEMORY-DESIGN.md) (#302): implement tenancy as the degenerate single-user case on SQLite first, roles at the storage boundary second, OIDC only in server mode third, and Postgres last — behind the explicit trigger in that document. Blocked on the maintainer accepting the proposal and answering its four open questions; identity must be designed before storage or the schema encodes single-principal assumptions. | #302 |

## Why these are deferred rather than started

Every item above except the Slack connector is gated on one of two design
decisions that are open on purpose:

- **Tenancy.** `LEVH_TOKEN` gates the whole server as a single principal — it
  identifies no user, workspace, or role. Team features and a hosted tier both
  build on that boundary, so starting either before it is settled would bolt
  identity onto a layer that has none. See #302.
- **Revenue.** Nothing in the tree can be charged for today, and the commercial
  surfaces (support/SLA, hosted sync, team workspace, SSO + metering) are ordered
  by how little they disturb the local-first core. See #298.

The connectors and the TypeScript SDK are the items that do **not** need either
decision first, which is why they are the cheapest to resume. Jira and Linear
landed on that reasoning: they cost no architecture, and the sync framework they
plug into already existed. The TypeScript SDK landed the same way: the contract
it generates from was already frozen.
