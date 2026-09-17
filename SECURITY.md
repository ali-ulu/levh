# Security Policy

LEVH is a local-first memory layer for AI agents and humans. Treat memory
databases as sensitive: they may contain project decisions, customer context,
private notes, or secrets a user pasted by accident.

## Supported versions

Security fixes target the current `main` branch first. Released versions are
fixed forward — install the latest release from PyPI to receive them.

| Version | Supported |
|---|---|
| Latest release | Yes |
| `main` | Yes (development) |
| Older releases | No — upgrade to the latest release |

## Reporting a vulnerability

Use GitHub's [private vulnerability reporting](https://github.com/ali-ulu/levh/security/advisories/new)
so the report stays confidential until a fix is ready. If you cannot use that,
contact the repository owner directly. Do not publish exploit details before
maintainers have had time to respond.

What to expect:

- **Acknowledgement** within 3 business days.
- **Initial assessment** (severity, affected versions) within 10 business days.
- **Fix or mitigation plan** within 30 days for confirmed high-severity issues.

Please include the LEVH version (`levh --version`), the platform, and the
smallest reproduction you can manage.

## Deployment guidance

- Keep LEVH bound to `127.0.0.1` or a trusted private network.
- Set `LEVH_TOKEN` before exposing `/api/*` beyond your own machine. When a
  token is set, the generated API docs (`/docs`, `/redoc`, `/openapi.json`) are
  withheld by default — re-enable them only on a trusted network with
  `LEVH_ENABLE_API_DOCS=true`.
- Keep `LEVH_CORS_ORIGINS` restricted to trusted origins.
- Avoid `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN=true` unless an external boundary
  (a firewall, a reverse proxy, or a loopback-only container publish) already
  prevents public access. It disables authentication for every peer.
- Do not commit `.env`, `stackmemory.db`, exported memories, logs, or generated
  runtime artifacts.
- Use `EMBEDDER_MODE=hash` only for tests and smoke demos; use `local`,
  `ollama`, or `openai` for real semantic quality.

## Legacy environment names

`STACKMEMORY_*` variables (for example `STACKMEMORY_TOKEN`) are accepted for
backward compatibility but are deprecated. Use the `LEVH_*` spelling — the
legacy names will be removed in a future release.
