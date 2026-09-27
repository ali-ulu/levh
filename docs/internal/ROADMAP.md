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
| 4 | Frontend i18n (more than one UI language) | deferred | Open a design issue before any code: scope is the ~20 pages plus the shared UI primitives, and the string-extraction mechanism is the decision that has to come first. | |
| 5 | End-to-end tests over a real server and browser | done | — | #299 |
| 6 | Page-level accessibility coverage (axe over each route) | done | — | #299 |
| 8 | Not recorded at decision time | skipped | — | |
| 9 | Not recorded at decision time | cancelled | — | |
| 10 | Not recorded at decision time | ignored | — | |
| 11 | Not recorded at decision time | skipped | — | |
| B | Commercial surfaces and a shared/team memory server | proposed | Maintainer decision on the two design issues; no code until the tenancy shape is agreed. | #298, #302 |

## Backlog workstreams

These are the deferred workstreams named alongside the report items. They are
grouped separately because they are recurring product surfaces rather than
numbered findings, and because each one is also a candidate revenue surface.

| Item | Topic | State | Next step | Reference |
| --- | --- | --- | --- | --- |
| connectors | Encrypted backup plus sync connectors (Jira, Linear, Slack, GitHub) for automatic memory accumulation | deferred | Decide whether sync is pull-on-demand or a background worker before writing a connector; `cryptography` is already a core dependency for passphrase-encrypted backups, so the encryption half is unblocked. | #298 |
| typescript-sdk | TypeScript SDK over the REST and MCP surface | deferred | Decide whether it is generated from `openapi.json` or hand-written; a generated client is the cheaper default and `openapi.json` is already committed. | #298 |
| multi-user-postgres | Multi-user auth, tenancy, and a Postgres backend | deferred | Blocked on the design issue; identity must be designed before storage or the schema encodes single-principal assumptions. | #302 |

## Why these are deferred rather than started

Every item above except the connectors is gated on one of two design decisions
that are open on purpose:

- **Tenancy.** `LEVH_TOKEN` gates the whole server as a single principal — it
  identifies no user, workspace, or role. Team features and a hosted tier both
  build on that boundary, so starting either before it is settled would bolt
  identity onto a layer that has none. See #302.
- **Revenue.** Nothing in the tree can be charged for today, and the commercial
  surfaces (support/SLA, hosted sync, team workspace, SSO + metering) are ordered
  by how little they disturb the local-first core. See #298.

The connectors and the TypeScript SDK are the two items that do **not** need
either decision first, which is why they are the cheapest to resume.
