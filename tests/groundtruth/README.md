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
| `test_cross_process_coherence.py` | Live readers sharing SQLite observe create, update and delete without restart. | Regression (marker removed) |
| `test_explicit_network_consent.py` | Ambient credentials alone produce zero Ask/Summary outbound attempts. | Regression (marker removed) |
| `test_update_admission_invariant.py` | Updated content passes admission before embedding or persistence. | Regression (marker removed) |
| `test_standalone_sse_auth_boundary.py` | Standalone SSE enforces configured `LEVH_TOKEN`. | Regression (marker removed) |

The process-heavy characterization harnesses that produced these invariants are
audit-only and are **not** part of this repository. They ran in the untracked
`evidence/` workspace used for Gate 0A (tasks 00A1-00A4) and were never tracked
here: `evidence/` is absent from the working tree and from `git ls-files`. There
is no in-repo path to run them from; the tests above are the durable contract.

If a characterization run is needed again, recreate it in an isolated audit
workspace outside this repo, with its documented network/process restrictions
and evidence directory. Heavy harnesses may rewrite task evidence and are not
release-gate CI tests.
