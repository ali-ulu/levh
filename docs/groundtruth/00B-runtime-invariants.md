# Gate 00B - Runtime Invariants for Remediation

This document separates six reviewable runtime contracts derived from the four
confirmed Gate 0A findings. It does not implement remediation. Every remediation
state remains `NOT_STARTED`.

Identity:

```text
TESTED_RUNTIME_SHA: 3a97ae7177c128e5484434d76828751330149fc3
PR_BASE_SHA: 034113e91feb442d480e9071612c50ce6092d486
AUDIT_BRANCH_HEAD: fe4242c60ac9c79b032861f3a88e293a95f60130
```

## 1. LIVE_CROSS_PROCESS_VISIBILITY

### Desired invariant

A committed create, content update or delete in one live LEVH process is visible
to every other live process using the same SQLite database without restarting
any process.

### Current status

`CONFIRMED_BROKEN` under P0-1. A live peer can miss creates, retain old update
content and return deleted ghost memories until restart.

### Evidence

- `evidence/groundtruth/task-00A1/engine-scenarios.jsonl`
- `evidence/groundtruth/task-00A1/transport-scenarios.jsonl`
- `docs/groundtruth/00A1-cross-process-coherence.md`

### Remediation acceptance test

Exercise create, update and delete in both directions across two simultaneously
live engines and real REST/MCP transports. Every peer must observe committed
state without restart, while SQLite integrity remains `ok`.

### Limitations / out of scope

The audit does not prescribe polling, invalidation, IPC or database-version
mechanics. Multi-host distributed databases are outside this local SQLite
contract.

## 2. EXPLICIT_OUTBOUND_CONSENT

### Desired invariant

```text
ambient credential present
+ no explicitly selected remote answer/summary provider
= zero outbound request
```

Ask, manual summary and session-end auto-summary must share this rule.

### Current status

`CONFIRMED_BROKEN` under P0-2. Ambient `OPENAI_API_KEY` activates HTTP
attempts and includes memory content even when the embedder is explicitly hash.

### Evidence

- `evidence/groundtruth/task-00A2/captured-httpx-posts.jsonl`
- `evidence/groundtruth/task-00A2/network-guard.txt`
- `docs/groundtruth/00A2-explicit-network-consent.md`

### Remediation acceptance test

With a synthetic ambient key and no explicit remote mode, intercept the HTTP
boundary and assert zero calls for Ask, summary and auto-summary. An explicit
remote selection must be visible separately for answer and summary providers.

### Limitations / out of scope

No real credential or real network request is permitted. Provider quality,
billing and remote-service availability are outside this consent invariant.

## 3. ADMISSION_ON_ALL_CONTENT_MUTATIONS

### Desired invariant

```text
user-controlled replacement content
-> admission and secret handling
-> embedding
-> SQLite/FTS persistence
```

### Current status

`CONFIRMED_BROKEN` under P0-3. Direct engine, REST PUT and MCP update can
embed and persist a raw synthetic secret canary while retaining stale admission
metadata from the safe create.

### Evidence

- `evidence/groundtruth/task-00A3/embedding-inputs.jsonl`
- `evidence/groundtruth/task-00A3/persistence-state.jsonl`
- `docs/groundtruth/00A3-content-update-admission-invariant.md`

### Remediation acceptance test

For every update surface, assert the raw canary is absent from embedding input,
SQLite, FTS and recall. Admission metadata must describe the current content.
Duplicate/review policy must not be bypassable through update.

### Limitations / out of scope

The audit does not choose whether rejected updates fail or persist redacted
content. That product decision must preserve the pre-embedding safety boundary.

## 4. STANDALONE_TRANSPORT_AUTHENTICATION

### Desired invariant

```text
LEVH_TOKEN configured
+ standalone SSE request without a valid token
= HTTP 401 before MCP initialization or tool execution
```

### Current status

`CONFIRMED_BROKEN` under P0-4. Standalone `server.mcp_sse:app` ignores the
configured token. The main FastAPI-mounted MCP remains middleware-protected.

### Evidence

- `evidence/groundtruth/task-00A4/scenarios.jsonl`
- `evidence/groundtruth/task-00A4/process-map.txt`
- `docs/groundtruth/00A4-standalone-sse-auth-boundary.md`

### Remediation acceptance test

Invoke the standalone ASGI surface with `LEVH_TOKEN` configured. Missing or
invalid credentials must receive 401 before initialize; a valid credential must
retain the intended profile surface. Recheck the mounted surface for parity.

### Limitations / out of scope

Loopback binding reduces exposure but is not authentication. TLS, reverse-proxy
configuration and public deployment policy are separate deployment concerns.

## 5. MCP_PROFILE_IS_NOT_AUTHORIZATION

### Desired invariant

Profile selection controls advertised capabilities only. It must never grant,
replace or imply client authentication. Every profile uses the same transport
authentication policy.

### Current status

`CONFIRMED_NON_EQUIVALENT_TO_AUTHORIZATION`. Gate 00A4 observed minimal,
work and full capability sets while unauthenticated standalone initialization
remained possible. Profile filtering did not enforce identity.

### Evidence

- `evidence/groundtruth/task-00A4/tool-surfaces.jsonl`
- `evidence/groundtruth/task-00A4/scenarios.jsonl`
- `tests/groundtruth/README.md`

### Remediation acceptance test

For minimal, work and full profiles, assert missing/invalid credentials are
rejected identically before initialize. With valid auth, assert only the
documented capability set changes between profiles.

### Limitations / out of scope

Exact tool membership and future profile names are capability-policy concerns.
This invariant only rejects treating a profile as an authentication boundary.

## 6. SQLITE_AND_DERIVED_STATE_CONVERGENCE

### Desired invariant

SQLite durable state and every in-memory/derived read model used for recall,
ranking, entities, trust or conflicts must converge after each committed
mutation without requiring process restart.

### Current status

`CONFIRMED_BROKEN_FOR_VECTOR_RECALL` under P0-1. SQLite remained current and
integrity checks passed while a live peer's vector state was stale. Other
derived models were not proven broken by Gate 00A1.

### Evidence

- `evidence/groundtruth/task-00A1/sqlite-state.txt`
- `evidence/groundtruth/task-00A1/engine-scenarios.jsonl`
- `docs/groundtruth/00A1-cross-process-coherence.md`

### Remediation acceptance test

After external create, update and delete commits, assert durable row state and
all affected derived readers converge in every live process. Include vector
recall plus targeted entity/trust/conflict invalidation tests where mutations
can affect those models.

### Limitations / out of scope

Gate 00A1 directly proves vector-cache divergence only. The broader derived
contract is a remediation acceptance requirement, not a claim that every
derived subsystem currently fails.

## Regression-test promotion

Each remediation PR must remove only its corresponding strict-xfail marker,
make the desired-invariant test pass, rerun the relevant isolated heavy harness,
and leave unrelated P0 markers in place.
