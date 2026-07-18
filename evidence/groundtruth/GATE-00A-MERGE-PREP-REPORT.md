# Gate 0A Merge-Preparation Report

## Result

```text
TASK-00A5:
COMPLETE_WITH_RECORDED_WINDOWS_LIMITATIONS

MERGE_PREPARATION:
READY_FOR_INDEPENDENT_DIFF_REVIEW

BACKEND_SUITE:
NOT_FULLY_GREEN_ON_WINDOWS_AUDIT_ENVIRONMENT

REMEDIATION:
NOT_STARTED
```

## Work completed

- Preserved all four accepted P0 verdicts and task reports.
- Moved process-heavy characterization programs out of default discovery into
  per-task `harness/` directories.
- Added six lightweight desired-invariant tests with `xfail(strict=True)`.
- Sanitized machine-specific worktree/temp paths and ephemeral PID/port values.
- Retained only explicitly synthetic, non-functional canaries needed to explain
  the privacy and admission findings.
- Normalized TASK-00A5 pytest output so a partial timeout stream cannot be
  mistaken for a completed run.
- Produced a checksum index for the complete review surface.

## Test results

```text
python -m pytest -q tests/groundtruth
6 xfailed

python -m pip check
No broken requirements found.

Backend characterization with two Windows-hanging tests deselected:
523 passed, 6 expected xfailed, 2 failed, 2 deselected
```

This is not a green backend-suite claim.

### Windows hangs

Both tests monkeypatch the process-global `socket.socket` constructor after an
async SQLite engine is initialized. On Windows, the Proactor event loop and
aiosqlite worker callback use the event-loop self-pipe. Replacing the socket type
breaks that mechanism and the SQLite await never completes.

Affected tests:

- `tests/test_dogfood_metrics.py::test_engine_listener_journals_ids_not_content`
- `tests/test_dogfood_wiring.py::test_product_surfaces_emit_dogfood_events`

### Reproducible failures

- MCP stdio blackbox: Windows named-pipe creation returns `WinError 5` under
  the audit sandbox.
- Setup CLI noninteractive test: command returns code 1 through unhandled
  `EOFError`, while the test expects an explicit `--demo or --real` message.

Neither failure is caused by an audit product-code diff because no product code
changed. They remain visible limitations, not passing results.

## Safety and scope

- No real OpenAI key or other real credential was used.
- No real outbound request was sent by Gate 0A network characterization.
- P0-4 servers bound only to loopback.
- No global purge, restore, admin mutation, commit, push, PR or merge occurred.
- No product code was modified.
- Allowed change roots are docs, evidence and Ground Truth tests only.

## Final verification

```text
groundtruth: 6 xfailed, exit 0
pip check: no broken requirements, exit 0
checksum index: 78 rows, 0 failures
forbidden artifacts: 0
machine-specific residuals: 0
real-secret pattern hits: 0
git status: 79 untracked audit files, 0 tracked changes, 0 outside scope
git diff --check: exit 0
```
## Decision boundary

This package may proceed to independent diff review. A local commit requires
separate user approval. No P0 remediation may start before the audit PR is
reviewed and merged.
