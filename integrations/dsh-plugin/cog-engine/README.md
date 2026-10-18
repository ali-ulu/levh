# Cognition Engine — deterministic reasoning tools for agents

A dependency-free DeepSeek Harness (DSH / Cordis) plugin that gives agents
"cognitive" tools that work like the deliberate half of a human brain: the
agent supplies the judgment, the plugin supplies the reliable computation.
Memory stays in LEVH; thinking lives here. No server changes, no build step,
no npm dependencies, no regex (CodeQL-clean by construction).

| Tool | What it computes |
|---|---|
| `cog_think` | Dual-process router: five signals (irreversibility, stake, ambiguity, novelty, time pressure) → deliberation demand → mandated protocol (System 1 act-now / hybrid / full System 2 with named tool steps). Makes the thinking mode an explicit, auditable choice. |
| `cog_deduce` | Forward-chaining deduction over facts + if-then rules to a fixpoint, with a step-by-step proof trace and paraconsistent contradiction detection (reports `X` vs `not X`, keeps chaining). |
| `cog_hypothesize` | Bayesian hypothesis testing: priors × per-evidence likelihoods → posteriors, leader, weak (non-discriminating) evidence, and which single observation would flip the ranking. |
| `cog_deliberate` | Pruned thought tree: idea branches under different cognitive frames get weighted composites; weak branches are pruned, near-duplicates are detected by Jaccard overlap, survivors are ranked. |
| `cog_decide` | Decisions under uncertainty: expected value, risk (sigma), risk-profile-adjusted score (averse / neutral / seeking), regret, and dominance checks. Complements `levh_decide`, which handles certain weighted-criteria scoring and records to LEVH. |
| `cog_bias_check` | Deterministic bias scan of a formed decision: anchoring, sunk-cost language, confirmation starvation, overconfidence, uniform score clusters, urgency pressure — each flag with a concrete debiasing action. |
| `cog_analogize` | Structure mapping between a familiar source and the current target: predicate-matched relations, gaps, and explicit transfer hypotheses with an alignment score. |
| `cog_reflect` | Post-decision learning loop: prediction error, overconfidence flag (stated confidence vs realized error), structured lesson composition, and optional storage into LEVH memory with error-scaled importance. |

## The intended loop

1. **Think** — `cog_think` decides how much deliberation the problem deserves.
2. **Orient** — `levh_ask_memory` (LEVH plugin) grounds the question in prior
   context.
3. **Deduce** — `cog_deduce` maps what actually follows from the known facts.
4. **Deliberate** — `cog_deliberate` fans out and prunes candidate ideas.
5. **Decide** — `cog_bias_check` scans the formed decision, then
   `cog_decide` (uncertainty) or `levh_decide` (certainty, which also records
   the decision and consults memory first via its preflight).
6. **Reflect** — `cog_reflect` closes the loop and files the lesson.

## Install

```text
plugin_manager install_bundle target="<repo>/integrations/dsh-plugin/cog-engine"
```

## Configuration

Optional row `config`: `baseUrl` (default `http://127.0.0.1:8000`),
`defaultProject`, `timeoutMs`, `token` — only `cog_reflect` talks to LEVH.
The other four tools are pure computation and work with no server at all.

## Design rules

- Judgment is the agent's; arithmetic and logic are the plugin's. Every tool
  fails loud on invalid input (probability sums, score ranges, missing
  likelihoods) instead of silently normalizing.
- Arguments arrive frozen from the tool registry; tools build normalized
  local copies and never mutate model input.
- Contradiction is a finding, not an exception: `cog_deduce` reports
  paraconsistent pairs and derives what still follows.
