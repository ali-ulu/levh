# LEVH Reasoner — a DeepSeek Harness (DSH) plugin

A dependency-free [DeepSeek Harness](https://github.com/deepseek-ai) (Cordis
bundle) plugin that gives every agent in a DSH profile four tools over LEVH's
existing REST API. No server changes, no build step, no npm dependencies.

- **`levh_decide`** — weighted-criteria decision support. The agent supplies
  options, criteria and per-option scores; the plugin does the deterministic
  math (ranking, margin, single-criterion sensitivity) and records the decision
  with `POST /api/v1/team/decisions`. When another agent later records a
  conflicting decision for the same project, LEVH flags it as **contested**.
- **`levh_guard_check`** — checks a proposed action against recorded guard
  rules (`POST /api/v1/guard/check`) *before* it runs. Advisory only: it
  returns `warn` when a rule overlaps the action and never hard-blocks.
- **`levh_ask_memory`** — asks the shared memory a question
  (`POST /api/v1/ask`) and returns a synthesized, cited answer grounded only
  in stored memories.
- **`levh_decision_conflicts`** — scans a project for open decision conflicts
  (`POST /api/v1/team/decision-conflicts/detect`).

## Requirements

- A running LEVH server (default `http://127.0.0.1:8000`, `levh serve`).
- A DSH profile whose version supports `plugin_manager install_bundle`.

## Install

```text
plugin_manager install_bundle target="<repo>/integrations/dsh-plugin/levh-reasoner"
```

where `<repo>` is the absolute path of this repository checkout. The bundle is
a plain directory package (no install scripts), so nothing needs approval
beyond the install call itself. DSH activates it immediately; verify with the
Tool inspector or by calling `levh_guard_check`.

## Configuration

Set values on the bundle row's `config` in your profile's `cordis.patch.yml`;
every field is optional and falls back to a default:

```yaml
- insert:
    - id: levh-reasoner
      name: '@local/levh-reasoner'
      config:
        baseUrl: http://127.0.0.1:8000
        defaultProject: my-project
        agentName: dsh-levh-agent
        timeoutMs: 8000
        token: ""
```

| Key | Default | Meaning |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8000` | LEVH base URL |
| `defaultProject` | `""` | project scope used when a tool call passes no `project` |
| `agentName` | `dsh-levh-agent` | name used for `POST /api/v1/agents/connect` bookkeeping |
| `timeoutMs` | `8000` | per-request timeout in milliseconds |
| `token` | `""` | sent as `Authorization: Bearer …` when the server sets `LEVH_TOKEN` |

## Security notes

- The default `baseUrl` is loopback. Point `baseUrl` at a remote host only if
  that host is yours; see the LEVH README's security section first.
- With `LEVH_TOKEN` set on the server, set the same value as `token`, or every
  call fails with `401`.
- `levh_decide` writes to `team/decisions` on every successful call unless
  `record: false` is passed (dry-run ranking).

Full documentation: [`docs/dsh-plugin.md`](../../docs/dsh-plugin.md).
