# Cognition Engine plugin

The [Cognition Engine](../integrations/dsh-plugin/cog-engine/README.md) is the
deliberate-thinking half of the DSH integration set: eight deterministic
tools that give agents real reasoning mechanics — deduction, Bayesian
hypothesis testing, pruned deliberation, decisions under uncertainty, bias
scanning, structure mapping, and a reflection loop that files lessons back
into LEVH memory. Memory stays in LEVH; thinking lives in the plugin. No
`server/` code is involved.

## Tools

| Tool | LEVH endpoint | What it computes |
|---|---|---|
| `cog_think` | none | Dual-process router: five signals → deliberation demand → mandated System 1 / hybrid / System 2 protocol with named tool steps. |
| `cog_deduce` | none | Forward-chaining deduction to a fixpoint with a proof trace; reports paraconsistent `X` vs `not X` pairs and keeps deriving. |
| `cog_hypothesize` | none | Bayesian update over hypotheses; posterior ranking, weak-evidence list, and single-observation flip analysis. |
| `cog_deliberate` | none | Pruned thought tree: weighted composites, threshold pruning, near-duplicate detection by Jaccard overlap. |
| `cog_decide` | none | Expected value, risk (sigma), risk-profile-adjusted score, regret, dominance — for probabilistic outcomes. |
| `cog_bias_check` | none | Deterministic bias scan: anchoring, sunk-cost, confirmation starvation, overconfidence, uniformity, urgency — each with a debiasing action. |
| `cog_analogize` | none | Structure mapping: predicate-matched relations, gaps, transfer hypotheses, alignment score. |
| `cog_reflect` | `POST /api/v1/memories` | Prediction error, overconfidence flag, structured lesson; stores into memory with error-scaled importance. |

## The intended loop

`cog_think` (how much deliberation?) → `levh_ask_memory` (what do we already
know?) → `cog_deduce` (what follows?) → `cog_deliberate` (what are the
options?) → `cog_bias_check` (what am I fooling myself about?) →
`cog_decide` / `levh_decide` (commit) → `cog_reflect` (file the lesson).

## Install

1. Run `levh serve` for `cog_reflect`; the other seven tools are pure
   computation and work without any server.
2. Call `plugin_manager install_bundle` with the absolute path of
   `integrations/dsh-plugin/cog-engine` inside this checkout.

## Design rules

- Judgment is the agent's; arithmetic and logic are the plugin's. Invalid
  input fails loud (probability sums, score ranges, missing likelihoods,
  signal ranges) instead of being silently normalized.
- Contradiction is a finding, not an exception: `cog_deduce` reports
  paraconsistent pairs and derives what still follows.
- No regex anywhere in the bundle, so the CodeQL polynomial-ReDoS class has
  nothing to flag.
