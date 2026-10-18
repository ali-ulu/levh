# DeepSeek Harness plugin

LEVH ships a [DeepSeek Harness](https://github.com/deepseek-ai) (DSH) plugin at
[`integrations/dsh-plugin/levh-reasoner/`](../integrations/dsh-plugin/README.md).
It registers four agent tools over the existing REST API, so a DSH session can
consult the mistake-guard rules, ground its reasoning in the memory store, and
record team decisions — the same surfaces the dashboard and MCP clients use.
No `server/` code is involved; the plugin is a plain JavaScript Cordis bundle
with zero npm dependencies.

## What the agent gains

| Tool | LEVH endpoint | What it does |
|---|---|---|
| `levh_decide` | `POST /api/v1/team/decisions` | Scores options against weighted criteria with deterministic math (ranking, margin, single-criterion sensitivity), then records the decision. A later, conflicting decision for the same project is returned as **contested** instead of being silently ignored. |
| `levh_guard_check` | `POST /api/v1/guard/check` | Judges a proposed action against recorded guard rules before it runs. Advisory: `warn` when a rule overlaps, `allow` otherwise; never a hard block. |
| `levh_ask_memory` | `POST /api/v1/ask` | Synthesized, cited answer grounded only in stored memories. |
| `levh_decision_conflicts` | `POST /api/v1/team/decision-conflicts/detect` | Counts open decision conflicts in a project, plus new candidates from the latest scan. |

## Install

1. Run `levh serve` (dashboard and API on `http://localhost:8000`).
2. In the DSH session, call
   `plugin_manager install_bundle` with the absolute path of
   `integrations/dsh-plugin/levh-reasoner` inside this checkout. A clean
   install reports `application: applied` with no warnings.
3. Confirm the four `levh_*` tools are visible to the agent.

## Configure

All four tools resolve their defaults from the bundle row's `config`
(`baseUrl`, `defaultProject`, `agentName`, `timeoutMs`, `token`); see the
[plugin README](../integrations/dsh-plugin/README.md#configuration) for the
table. With `LEVH_TOKEN` set on the server, put the same value in `token`.

## Behavior notes

- `levh_decide` performs the arithmetic; the agent performs the judgment. It
  validates that every criterion carries a positive weight and every option
  scores every criterion in `[0, 10]`, and fails loudly otherwise.
- `levh_decide` runs a **preflight consult** before recording: it asks
  `POST /api/v1/ask` about the decision question and cites the prior context
  in the recorded rationale. Opt out with `preflightAsk: false` in the row
  config.
- `levh_decide` passes `record: false` for a dry-run ranking that records
  nothing (the preflight consult still runs when a project scope resolves);
  otherwise the decision is recorded under the resolved project with
  a slug of the question as its `decision_key` (override with `decision_key`).
- `levh_guard_check` is advisory by design: a `warn` is an instruction the
  agent should heed, and the caller decides what to do.
- Every request times out after `timeoutMs` and reports the failure to the
  agent instead of hanging the session.
- For the reasoning half of the integration set — deduction, hypothesis
  testing, deliberation, bias scanning, and reflection — see the
  [Cognition Engine plugin](cog-engine.md).

## Security

The plugin defaults to loopback (`http://127.0.0.1:8000`) and sends no
credentials unless a `token` is configured. It reads only from LEVH's REST
surface and writes only to `team/decisions`; it never touches attachments,
connectors, or exports.
