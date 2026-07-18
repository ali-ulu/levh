# Gate 0A Merge Manifest

## Preparation decision

```text
TASK-00A5_GATE_0A_MERGE_PREPARATION:
COMPLETE_WITH_RECORDED_WINDOWS_LIMITATIONS

READY_FOR:
INDEPENDENT_DIFF_REVIEW

NOT_AUTHORIZED:
COMMIT, PUSH, PR, MERGE, REMEDIATION
```

Locked source:

- HEAD: `3a97ae7177c128e5484434d76828751330149fc3`
- Branch: `audit/groundtruth-v2`
- Product-code changes: none
- Allowed roots: `docs/groundtruth/`, `evidence/groundtruth/`,
  `tests/groundtruth/`

## Included document layer

- Four task reports: `00A1` through `00A4`
- Consolidated findings: `00A-p0-runtime-findings.md`
- Desired contracts: `00B-runtime-invariants.md`
- This manifest
- Gate evidence index and merge-preparation report

## Included test layer

- Four audit-only heavy harnesses under task evidence `harness/`
- Four lightweight test modules under `tests/groundtruth/`
- Six total `xfail(strict=True)` desired-invariant tests
- `tests/groundtruth/README.md` policy

## Validation record

```text
pytest collection: 533 tests
groundtruth: 6 xfailed, 0 failed, 0 xpassed
pip check: No broken requirements found
backend: 523 passed, 6 expected xfailed, 2 failed, 2 deselected
backend suite green: NO
real-secret pattern hits: 0
machine-specific path/PID/port residuals: 0
forbidden db/wal/shm/zip/cache artifacts after cleanup: 0
tracked product changes: 0
```

The Windows limitations and the two baseline failures are described in
`GATE-00A-MERGE-PREP-REPORT.md`. Checksums are recorded in
`GATE-00A-EVIDENCE-INDEX.md`; the index excludes its own checksum.

## Review sequence

1. Independent diff and evidence review.
2. Separate approval for a local audit commit.
3. Separate approval for push.
4. Audit PR.
5. Separate merge approval.
6. Four independent remediation PRs only after the audit PR is merged.
