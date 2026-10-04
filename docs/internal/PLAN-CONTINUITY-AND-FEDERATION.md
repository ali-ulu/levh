# Plan: proving continuity, then federating it

Status: proposal · Owner: maintainers · Scope: two next workstreams

This is a plan, not a decision record: it names what will be built, in what
order, and what would make us stop. The decision states for the report items
live in [`ROADMAP.md`](ROADMAP.md); the tenancy and federation architecture
questions live in #302, #309 and #298. Nothing here is a promise until a row
lands in one of those.

The two workstreams answer one question from two ends:

- **Continuity proves the core works in real use.** The product claims sessions
  start already briefed. That claim is currently backed by construction
  (the brief is printed at startup; hooks are installed), not by measurement
  of whether the agent actually *used* the memory and got better answers.
- **Federation proves the core travels.** The documented next architecture
  step is a shared/team memory server, and the first thing that breaks is the
  single-process assumption. Building federation before the continuity surface
  is instrumented means carrying unmeasured behavior across a process boundary
  and never being able to attribute a regression to either side.

Ordering: **Phase A first, Phase B second.** A is cheap, offline, and produces
the before/after numbers B needs to show federation did not damage recall.

---

## Phase A — prove continuity end to end

### A0. Reconcile the brief's transport inventory

The multi-channel story is real (stderr bridge, native session-start hooks,
tool/resource) but it is stated unevenly across the tree: `universal_hooks.py`
and `docs/mcp-client-config.md` document the opt-in/default flags, while the
README's per-client list is prose. Nothing checks the three channels against
the code, so a client added to one list and not another drifts silently.

Next step: a single table — client × channel (stderr / hook / tool) × default —
kept honest by a test that reads the same registries the installers read.

### A1. Measure whether continuity is emitted and used

This is the highest-value gap: feature count is not evidence. Two signals, both
offline and already partially present:

- **Emission — a proxy for delivery, not delivery itself.** `LEVH_AUTO_BRIEF`
  prints the brief to stderr and a hook runs `levh continue --limit 5 --if-any`;
  both are producer-side events we can count (the stderr line was written, the
  hook call succeeded and produced output). Neither proves the agent read it:
  that is the client's behavior, outside our process. Name the counter
  accordingly (`briefs emitted`, not `briefs delivered`) so the number cannot be
  read as a delivery guarantee.
- **Use.** The engine already records recall events and reinforces recalled
  memories. A continuity-specific counter (briefs whose suggested memories were
  then recalled, `recall_memory` calls in the first N turns of a session) is the
  only signal that speaks to use rather than emission, and turns "the agent
  starts briefed" from a claim into a number.

Next step: extend the golden-fixture evaluator
(`server/core/evaluation.py`, `tests/fixtures/evaluation/*.json`) with a
continuity scenario: store a checkpoint + a pinned rule + a blocker, then assert
the brief surfaces them in that order and that a subsequent recall reinforces
the surfaced memory. Determinism and privacy contracts already apply.

### A2. Close the weak link honestly — landed

Option 2 is selected and implemented in #423: both stdio and SSE MCP servers
publish one shared server `instructions` directive telling the client to call
`get_continuity_brief` at the start of a work session. The tool belongs to the
minimal profile, so the instruction never advertises a tool the selected profile
cannot call. Black-box protocol tests assert the directive appears in each
transport's initialize result.

The existing AGENTS.md rule and stderr bridge remain complementary fallbacks.
No polling tool was added: polling would increase tool surface without creating
a push channel. A1's emitted/use counters remain the measurement for whether the
continuity material is actually used; the instructions field improves the
no-hook client path without pretending it proves consumption.

---

## Phase B — federate, and keep the core measurable

Federation is a protocol decision inside #302's tenancy design, not a new
product surface invented here. The minimum viable shape, mapped to code that
already exists:

| Concern | Where it lives today | Federation requirement |
| --- | --- | --- |
| Provenance of a memory | `Memory.source`, `metadata`, `server/core/trust.py` | Carry the *origin instance* and confidence, not just a source type. |
| Survives the boundary | `server/core/crypto.py` (Fernet envelope, `MAGIC` header) | Sign the envelope so an exchanged memory is attributable, not merely encrypted. |
| Import path | `server/core/engine/transfer.py` — `export_memories` / `import_memories_gated` | A peer's bundle enters through the *same* admission gate; nothing bypasses it. |
| Receiver re-scoring | `H(x,ψ)` in `server/core/hscore.py`, decay in `server/core/engine/decay.py` | An imported memory arrives with the sender's importance but decays on the receiver's clock — "your important" becomes "my candidate". |
| Conflict | `server/core/conflict.py` + `conflict_service.py` | Disagreements between peers are conflict *candidates*, reviewed, never auto-merged. |
| Bypass risk | single-process assumption (`docs/ARCHITECTURE.md` §1) | The exchange is a pull/push of bundles, not a shared live store — the engine stays single-process. |

The honest warning from the architecture analysis stands: this is the hardest
option, because it forces the provenance model and the trust model to be
designed on purpose rather than inherited from a single principal.

### B0. A federation envelope, offline first — complete

The signed offline envelope is complete through #338/#356/#357 and the B0
acceptance proof is closed by #425. Federation import verifies the envelope
before writing, then stamps the verified node id, signature algorithm and
envelope timestamp into every candidate's `metadata.federation`, overwriting
any sender-supplied spoofed value. One deterministic bundle test drives
`import_memories_gated` through admitted, rejected and review-held outcomes:
admitted entries persist with verified provenance, rejected entries do not
persist, and review-held entries retain the same provenance in
`held_memories`. No network transport is introduced in B0.

### B1. Exchange transport

Only after B0: a pull-first transport (instance A fetches B's bundles) rather
than a shared store, so the single-process engine is untouched. This is where
the tenancy decision (#302) must be settled, because identity determines who may
pull from whom.

### B2. Measure that federation preserved recall quality

Re-run Phase A's continuity scenario across two instances and assert the
imported memory is recalled on the receiver, decays on the receiver's clock, and
never outranks a local memory the receiver pinned. Next step: fixture in
`tests/fixtures/evaluation/` once B1 exists.

---

## What would make us stop

- If A1 shows agents already call `recall_memory` reliably before the prompt,
  A2 shrinks to documentation and no adapter is written.
- If B0 shows the admission gate cannot accept a peer bundle without a
  provenance field that does not exist yet, the tenancy design (#302) grows a
  required section before any transport work starts.
- If federation cannot be expressed without a shared live store, it is out of
  scope for the single-process core and belongs in the hosted tier (#298).
