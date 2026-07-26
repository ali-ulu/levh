# Ground Truth invariant tests

This directory contains lightweight **desired-behaviour** tests for the four
Gate 0A P0 findings. They are part of normal backend collection. Tests remain
marked `xfail(strict=True, raises=AssertionError)` while their corresponding
product defect is open, then become ordinary regression tests when remediated.

Only an `AssertionError` from the desired invariant may be an expected failure.
Infrastructure, import, SQLite and harness errors remain real failures. An
unexpected pass is a hard CI failure, forcing the remediation to remove the
marker and promote the test to an ordinary regression test.

| Test file | Desired invariant | Current marker |
|---|---|---|
| `test_cross_process_coherence.py` | Live readers sharing SQLite observe create, update and delete without restart. | P0-1 strict xfail |
| `test_explicit_network_consent.py` | Ambient credentials alone produce zero Ask/Summary outbound attempts. | Regression (marker removed) |
| `test_update_admission_invariant.py` | Updated content passes admission before embedding or persistence. | Regression (marker removed) |
| `test_standalone_sse_auth_boundary.py` | Standalone SSE enforces configured `LEVH_TOKEN`. | Regression (marker removed) |

The process-heavy characterization harnesses are audit-only and live beside
their immutable evidence:

- `evidence/groundtruth/task-00A1/harness/reproduce_cross_process_coherence.py`
- `evidence/groundtruth/task-00A2/harness/reproduce_explicit_network_consent.py`
- `evidence/groundtruth/task-00A3/harness/reproduce_update_admission_invariant.py`
- `evidence/groundtruth/task-00A4/harness/reproduce_standalone_sse_auth_boundary.py`

They are excluded from normal pytest discovery by location and filename.
Run one explicitly only in an isolated audit workspace, with its documented
network/process restrictions and evidence directory. Heavy harnesses may
rewrite task evidence and are not release-gate CI tests.
