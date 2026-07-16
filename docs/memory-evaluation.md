# Memory Evaluation & Dogfood Journal (2.25)

Two offline measurement tools added in 2.25, both local-only and both
distinct from the H(x,ψ) recall benchmark (`levh benchmark`).

## Golden-fixture evaluation

`server/core/evaluation.py` runs a fixed set of golden fixtures
(`tests/fixtures/evaluation/*.json`, 9 scenarios) through the real pipeline —
admission gate → store → trust recompute → conflict detection → recall →
review — on a fresh throwaway store, no LLM, no mocks. Every number in the
report comes from executing the same code paths a live install runs.

Run it with `levh eval run [--fixtures DIR] [--embedder-mode MODE]
[--output FILE]` (writes `eval_report.json` by default) and read the last
report with `levh eval report [--output FILE]`.

**Determinism contract**: with the hash embedder and a fixed fixture set, two
consecutive runs produce byte-identical reports. Fixture keys stand in for
generated memory ids; nothing time- or random-dependent appears in the
report.

**Privacy contract**: the report contains fixture keys, scenario names,
labels, and numbers only — never the raw memory content that was stored.

### Fixture JSON schema

Each fixture file is one JSON object:

| Field | Meaning |
|---|---|
| `name` | Scenario name (defaults to the filename stem if omitted) |
| `memories` | List of memory items to admit, each with `key`, `content`, `source`, `importance`, `tags`, `project`, `pinned`, `memory_type`, `metadata`, and `expected_admission` (the admission-gate action the fixture expects: `admit` / `review` / `reject` / `redact`) |
| `queries` | Recall queries: `query` text, `expected_memory_keys` (keys that should be recalled), `forbidden_memory_keys` (keys — typically superseded facts — that must not outrank the expected ones) |
| `expected_conflicts` | List of `[key_a, key_b]` pairs the conflict detector is expected to flag as candidates |
| `expected_conflict_count` | Optional exact count check on detected conflict candidates |
| `expected_trust_labels` | Map of `key` → expected trust label, or a list of acceptable labels (e.g. `["medium", "medium_high"]`) when the hash embedder puts a fixture one threshold either side |
| `review_actions` | Declared human review actions to apply through the real review path, each with `key` and `action` |
| `post_review_queries` | Recall queries run *after* `review_actions`, to check fading-memory recovery (same shape as `queries`, evaluated as hit/no-hit) |
| `reinforce_before_eval` | Keys to reinforce before trust/conflict computation, modeling "a confirmed human memory" rather than a memory that was merely stored |
| `known_false_positives` | If `true`, this fixture's detected conflict false positives count in the aggregate but do not fail the fixture — used by the one fixture (`09_conflict_false_positive_guard.json`) that deliberately measures a known false-positive case rather than hiding it |

### Report structure

```
{
  "evaluation_version": "memory-eval-v1",
  "levh_version": "...",
  "embedder_mode": "hash",
  "fixture_count": 9,
  "fixtures": [{"name": "...", "passed": true}, ...],
  "recall": {"queries": N, "hit_at_1": ..., "hit_at_3": ..., "mrr": ..., "forbidden_violations": N},
  "quality": {
    "items": N, "admission_accept_rate": ..., "review_rate": ...,
    "reject_rate": ..., "redaction_rate": ..., "duplicate_rate": ...,
    "admission_mismatches": N, "checked_low_trust_count": N  (only fixture-checked memories), "trust_label_mismatches": N
  },
  "conflicts": {"expected": N, "detected": N, "precision": ..., "recall": ..., "false_positives": N},
  "lifecycle": {"review_distribution": {...}, "fading_recovery_rate": ...},
  "product": {"seed_demo": {"completed": bool, "memories": N, "conflict_candidates": N}}
}
```

Every value in a report is tied to `evaluation_version` and the fixture set
that actually produced it. There are no fabricated or hard-coded numbers in
this codebase's docs — quote a metric only from a real `levh eval
run` against a known fixture set.

## Dogfood journal

`server/core/dogfood.py` is a local, append-only JSONL journal of coarse
usage events (`dogfood_events.jsonl` by default, overridable with
`DOGFOOD_JOURNAL_PATH`). Since 2.25.1 live wiring is opt-in via
`LEVH_DOGFOOD_ENABLED=true`: the shared engine provider then attaches
the journal (default location: next to the SQLite database) and the briefing,
meeting-prep, trust-view, review, and seed-demo surfaces emit events
automatically; a per-engine guard makes double attach a no-op — the mechanism the Editor uses to check whether the
product is actually helping, from real signals instead of vibes.

Hard rules:

- **Local-only, no network** — the module performs no network or socket I/O.
- **No default telemetry** — nothing is recorded unless the running install
  explicitly attaches the journal to the engine (`DogfoodJournal.attach`).
- **No raw memory content** — events carry an event type, a timestamp, and a
  small whitelist of scalar attributes (`memory_id`, `conflict_id`, `count`,
  `label`, `project`). Anything else is rejected at the API boundary
  (`record()` raises on an unknown event type or attribute).
- **Export is an explicit user action** — `export()` writes an *aggregate*
  status report; raw event lines never leave the journal file on their own.

Whitelisted event types: `memory_stored`, `memory_recalled`,
`recall_helpful`, `recall_not_helpful`, `trust_viewed`, `conflict_dismissed`,
`conflict_confirmed`, `meeting_prep_opened`, `briefing_opened`,
`review_keep`, `review_reinforce`, `review_weaken`, `review_forget`,
`seed_demo_completed`.

CLI: `levh dogfood status` prints the aggregate view (event counts,
time-to-first-value for first recall/briefing/meeting-prep after the journal
starts, recall-feedback helpful rate, review-action distribution).
`levh dogfood export --output report.json` writes that same aggregate
to a file.

## Non-claims

- MCP tool profiles (`minimal`/`work`/`admin`/`full`) reduce what's
  *advertised* to a client for tool-selection accuracy. They are not
  mentioned here as a security feature and none of this evaluation or
  journal machinery treats them as one.
- No specific metric values are asserted in this document. Numbers only ever
  come from a report produced by `levh eval run`.
