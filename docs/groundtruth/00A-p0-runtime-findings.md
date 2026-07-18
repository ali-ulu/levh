# Gate 0A - Consolidated P0 Runtime Findings

## Gate status

```text
GATE_00A_RUNTIME_CHARACTERIZATION:
COMPLETE

REMEDIATION:
NOT_STARTED
```

Runtime evidence identity and PR identity are intentionally separate:

```text
TESTED_RUNTIME_SHA: 3a97ae7177c128e5484434d76828751330149fc3
PR_BASE_SHA: 034113e91feb442d480e9071612c50ce6092d486
AUDIT_BRANCH_HEAD: fe4242c60ac9c79b032861f3a88e293a95f60130
```

`AUDIT_BRANCH_HEAD` is the post-main-merge, pre-correction snapshot. Runtime
findings remain tied to `TESTED_RUNTIME_SHA`; no product code was modified by
this audit.

## Canonical findings

| ID | Canonical verdict | Runtime boundary | Evidence |
|---|---|---|---|
| P0-1 | `CROSS_PROCESS_CREATE_UPDATE_DELETE_COHERENCE_BROKEN` | Two live engines/transports share SQLite but retain process-local stale vector state. Restart reloads correct SQLite state. | `task-00A1/` |
| P0-2 | `AMBIENT_OPENAI_KEY_ACTIVATES_OUTBOUND_MEMORY_TRANSMISSION` | Ambient `OPENAI_API_KEY` activates Ask, Summary and auto-summary HTTP attempts even with hash embeddings. No real network was used in the audit. | `task-00A2/` |
| P0-3 | `CONTENT_UPDATE_BYPASSES_ADMISSION_AND_SECRET_REDACTION` | Direct engine, REST PUT and MCP update can embed and persist a raw synthetic secret canary without rerunning admission. | `task-00A3/` |
| P0-4 | `STANDALONE_MCP_SSE_IGNORES_CONFIGURED_LEVH_TOKEN` | Standalone SSE accepts an unauthenticated client even when `LEVH_TOKEN` is configured. FastAPI-mounted MCP remains middleware-protected. | `task-00A4/` |

These are runtime-confirmed invariant violations, not remediation claims.
All four remediation states remain `NOT_STARTED`.

## Test policy

The original process-heavy reproduction programs are preserved as audit-only
harnesses under each task's `harness/` directory. Their filenames begin with
`reproduce_` and they are outside default pytest discovery.

Normal backend collection contains six lightweight desired-invariant tests.
They are marked `xfail(strict=True)`. Expected failure records the open defect;
an unexpected pass fails CI and requires the remediation PR to remove the marker
and promote the test to an ordinary regression test.

Targeted result:

```text
6 xfailed
0 failed
0 xpassed
```

## Backend suite status

```text
Backend suite: NOT FULLY GREEN ON WINDOWS AUDIT ENVIRONMENT

523 passed
6 expected xfailed
2 failed
2 deselected
```

The two deselections prevent deterministic Windows hangs caused by tests that
replace the global `socket.socket` type while aiosqlite must signal the
Proactor event loop self-pipe:

- `tests/test_dogfood_metrics.py::test_engine_listener_journals_ids_not_content`
- `tests/test_dogfood_wiring.py::test_product_surfaces_emit_dogfood_events`

The two reproducible failures are:

- `tests/test_mcp_blackbox.py::test_mcp_stdio_protocol_blackbox`:
  Windows named-pipe creation is denied with `WinError 5` in this sandbox.
- `tests/test_setup_cli.py::test_setup_requires_explicit_mode_when_noninteractive`:
  the command raises `EOFError` instead of emitting the expected
  `--demo or --real` guidance.

Product code is unchanged, so these are not audit-diff regressions. They are
recorded limitations and the suite is not described as green.

## Review-closeout boundary

```text
AUDIT_COMMIT: COMPLETED
BRANCH_PUSH: COMPLETED
INDEPENDENT_DIFF_REVIEW: CHANGES_REQUESTED_THEN_CORRECTED
PR: NOT_STARTED
MERGE: NOT_AUTHORIZED
REMEDIATION: NOT_STARTED
```

The original TASK-00A5 no-commit/no-push boundary is a historical preparation
snapshot. Commit and push were subsequently authorized and completed. TASK-00A6
may create a draft audit PR after validation; it does not authorize merge or P0
remediation.
