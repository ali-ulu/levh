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
| `continuity` | The continuity scenario (#378): `checkpoint` (`agent_name`/`title`/`summary`, created through the real tracker path before signals are read), `project`, `expect_checkpoint`, `expected_surfaced_order` (fixture keys, asserted in brief presentation order), `recall_after_brief_key` + `recall_query` (a surfaced memory must be recalled AND reinforced afterwards) | |

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
  "continuity": {"briefs": N, "checkpoint_surfaced": bool, "order_ok": bool, "use_ok": bool},
  "product": {"seed_demo": {"completed": bool, "memories": N, "conflict_candidates": N}}
}
```

### Continuity: emission vs use (#378)

The product claims sessions start already briefed. That claim has two
separable parts, measured at two layers:

- **Emission** — a producer-side event. `continuity_log` (schema v5) records
  one row per brief handed out, with the channel (`stderr_bridge`,
  `mcp_tool`, `session_hook`, `cli`) and the memory ids the brief surfaced,
  in presentation order. The counter is named "emitted" on purpose: printing
  a brief proves it was handed out, nothing about whether the agent read it.
  Live counts: `GET /api/config` → `continuity_briefs`, including
  `recalls_in_use_window` — how many surfaced memories a later recall
  returned within 24h, the store-scale use signal.
- **Use, fixture-level** — the `continuity_brief_surface_and_use` scenario
  asserts the brief surfaces its checkpoint, pinned rule and tagged blocker
  in brief order, and that a subsequent recall returns and reinforces a
  surfaced memory. This is the deterministic form of "the brief is emitted
  and used"; the report's `continuity` block carries it, and no value in it
  may be read as a delivery guarantee.

Per the plan (`docs/internal/PLAN-CONTINUITY-AND-FEDERATION.md` A1→A2): if
live data shows agents already recall reliably, the no-hook-client story
stays documentation — no adapter is written.

Every value in a report is tied to `evaluation_version` and the fixture set
that actually produced it. There are no fabricated or hard-coded numbers in
this codebase's docs — quote a metric only from a real `levh eval
run` against a known fixture set.

## External benchmark: LoCoMo, retrieval-only

The golden-fixture evaluator above is *self-authored*: it answers "did this
change regress ranking on our own scenarios". It cannot answer "how good is
LEVH at long-term memory compared to anything else". `server/core/external_benchmark.py`
adds one external yardstick for the honest subset that needs **no LLM judge** —
retrieval-side metrics on the public **LoCoMo** benchmark
([snap-research/locomo](https://github.com/snap-research/locomo)).

Run it with `levh benchmark-locomo --data /path/to/locomo10.json
[--embedder-mode MODE] [--top-k K] [--limit N] [--json] [-o FILE]`. The dataset
is **not vendored and never downloaded** — it is a research release, so point
`--data` at a checkout. With `--embedder-mode hash` the run is deterministic:
the committed artifact
`tests/fixtures/external_benchmark/locomo10_retrieval_hash.json` is
byte-identical to a fresh run.

### Protocol

Each conversation is fed through the real pipeline — `admit_memory` (the
admission gate) into a per-sample throwaway store, then `recall` — one memory
per dialogue turn, content `"speaker: text"`, tagged with its `dia_id`. A
question counts as a **hit** when any turn LoCoMo labels as evidence is
retrieved within top-k. Turns are pinned so wall-clock decay cannot flip
near-ties; that keeps retrieval, which is what this measures, separate from the
decay simulation.

### What it reports, and what it does not

- `retrieval` — recall@1/3/5/10 and MRR over categories 1–4 (the ones with an
  evidence label), plus a per-category breakdown.
- `adversarial` — category 5, reported as an **evidence-retrieval proxy**, not
  an abstention rate. LoCoMo does not label an adversarial question as absent
  from the conversation, and a boolean `recall` cannot decline to answer.
  Answer abstention needs an LLM judge and is deliberately out of scope.
- `admission` — the gate's action distribution over the ingested turns.

### Measured (hash embedder, `top_k=10`, full release)

The committed artifact records one real run:

| Metric | Value |
|---|---|
| conversations / turns | 10 / 5882 |
| scored questions | 1536 |
| hit@1 / hit@3 / hit@5 / hit@10 | 0.3581 / 0.5072 / 0.5671 / 0.6471 |
| MRR | 0.4486 |
| adversarial evidence-retrieval (proxy) | 0.5628 |

These are a **lexical floor**, not a semantic result: the hash embedder is
positional, not semantic. A semantic embedder (`local`/`openai`) is expected to
move these numbers and must be measured on a machine that has the model. The
report is a reproducible artifact, not a CI gate — it needs the dataset and a
full run is long, so it gates nothing and is run on demand.

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
