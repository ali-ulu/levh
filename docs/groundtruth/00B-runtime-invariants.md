# Gate 00B - Runtime Invariants for Remediation

This document converts the four confirmed Gate 0A findings into desired product
contracts. It does not implement them. Every remediation remains `NOT_STARTED`.

## P0-1 - Live cross-process coherence

Desired invariant:

```text
A committed create, content update, or delete in one live LEVH process
must be visible to every other live process using the same SQLite database
without restarting any process.
```

Acceptance requires create visibility, fresh updated content, absence of deleted
ghost memories, both writer directions, REST/MCP transport coverage, and
restart-independent correctness. SQLite must remain the durable source of truth;
cache invalidation or refresh must not weaken transaction integrity.

## P0-2 - Explicit outbound consent

Desired invariant:

```text
OPENAI_API_KEY present
+ no explicitly selected remote answer/summary provider
= zero outbound request
```

Ask, manual summary and session-end auto-summary must share the same policy.
Answer and summary provider modes must be explicit and visible. The default must
be local/extractive. Ambient credentials alone must not transmit memory content.

## P0-3 - Admission on every content mutation

Desired invariant:

```text
user-controlled replacement content
-> admission and secret handling
-> embedding
-> SQLite/FTS persistence
```

No update surface may persist or embed a raw rejected/redacted candidate.
Admission metadata must describe the current content, not the original create.
Direct engine, REST and MCP paths must enforce one mutation policy. Duplicate and
review decisions must not be bypassable by update.

## P0-4 - Standalone SSE authentication

Desired invariant:

```text
LEVH_TOKEN configured
+ standalone SSE request without a valid token
= HTTP 401 before MCP initialization or tool execution
```

Profile selection remains capability filtering and must not be presented as
authentication. Standalone and FastAPI-mounted MCP must apply equivalent token
semantics. Bind-address guidance is defense in depth, not a replacement for auth.

## Regression-test promotion

Each remediation PR must:

1. remove the corresponding `xfail(strict=True)` marker;
2. make the lightweight desired-invariant test pass;
3. run the relevant heavy harness in an isolated audit workspace;
4. preserve sanitized evidence without real credentials or real outbound calls;
5. avoid combining unrelated P0 remediations in one PR.
