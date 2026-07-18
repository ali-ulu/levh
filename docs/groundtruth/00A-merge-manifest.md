# Gate 0A Merge Manifest

## Review-closeout status snapshot

```text
TASK-00A6_GATE_0A_REVIEW_CLOSEOUT:
CORRECTIONS_APPLIED_VALIDATION_COMPLETE

AUDIT_COMMIT:
COMPLETED
c0f0858c73525463b8230c34b8adc61721f64e2d

BRANCH_PUSH:
COMPLETED

INDEPENDENT_DIFF_REVIEW:
CHANGES_REQUESTED_THEN_CORRECTED

PR:
NOT_STARTED

MERGE:
NOT_AUTHORIZED

REMEDIATION:
NOT_STARTED
```

## Source identities

```text
TESTED_RUNTIME_SHA:
3a97ae7177c128e5484434d76828751330149fc3

PR_BASE_SHA:
034113e91feb442d480e9071612c50ce6092d486

AUDIT_BRANCH_HEAD:
fe4242c60ac9c79b032861f3a88e293a95f60130
```

`AUDIT_BRANCH_HEAD` is the post-main-merge, pre-review-correction snapshot.
The final correction commit and live PR head are recorded in
`evidence/groundtruth/task-00A6/REVIEW-CLOSEOUT.md` and GitHub metadata.

The P0 runtime evidence remains tied to `TESTED_RUNTIME_SHA`. Updating the PR
base does not relabel or rerun that historical runtime characterization.

## Scope

- Branch: `audit/groundtruth-v2`
- Product-code changes relative to `PR_BASE_SHA`: none
- Allowed roots: `docs/groundtruth/`, `evidence/groundtruth/`,
  `tests/groundtruth/`
- Main integration: approved `git merge --no-ff origin/main`; no force push

## Included document layer

- Four task reports: `00A1` through `00A4`
- Consolidated findings: `00A-p0-runtime-findings.md`
- Six desired contracts: `00B-runtime-invariants.md`
- This manifest
- Gate evidence index and merge-preparation/closeout reports

## Included test layer

- Four audit-only heavy harnesses under task evidence `harness/`
- Four lightweight test modules under `tests/groundtruth/`
- Six total `xfail(strict=True, raises=AssertionError)` tests
- Only desired-invariant assertion failures may become XFAIL
- Infrastructure, import, SQLite and harness errors remain real FAIL results

## Validation policy

```text
groundtruth target: exactly 6 expected xfailed
groundtruth failures: 0
groundtruth XPASS: 0
xfail exception boundary: AssertionError only
pip check: clean
product-code diff: 0
forbidden artifacts: 0
real-secret hits: 0
checksum validation: 100%
```

The previously recorded Windows backend result is retained and is not green:

```text
523 passed
6 expected xfailed
2 failed
2 deselected
```

TASK-00A6 validation completed in one accepted iteration:

```text
pytest tests/groundtruth: 6 xfailed, 0 failed, 0 XPASS
pytest collection: 533 tests
pip check: No broken requirements found
git diff --check: clean
checksum verification: 80/80 indexed files
product-code diff: 0
forbidden artifacts: 0
real-secret hits: 0
```

## Historical preparation record

TASK-00A5 originally ended before commit and push authorization. Those actions
were subsequently authorized and completed as commit `c0f0858...` and branch
`origin/audit/groundtruth-v2`. The old "commit/push not authorized" statement
is historical only and is superseded by the status snapshot above.

## Remaining sequence

1. Complete TASK-00A6 validation loop.
2. Commit and normal fast-forward push; never force push.
3. Create a draft audit PR from `audit/groundtruth-v2` to `main`.
4. Independent PR diff/CI/mergeability review.
5. Separate merge authorization.
6. Four independent remediation PRs only after the audit PR is merged.
