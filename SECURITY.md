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

## Federation envelopes

`levh export-full --sign` wraps a full-export bundle in a signed envelope and
`levh import-full` verifies it before anything reaches the store. This is a
deliberate widening of the threat model: `SECURITY.md` otherwise assumes a
single trusted local file, and importing from a peer means accepting untrusted
input.

- **The admission gate is the boundary.** A verified envelope proves *who*
  produced the bundle, not that its contents are safe. Every imported memory
  still re-enters through `import_memories_gated` — dedupe, secret redaction,
  review-holding — exactly like a local write. Verification happens first, so a
  tampered or unverifiable bundle imports nothing at all.
- **Verify with an explicit key.** `levh import-full --key <public.pem>` is the
  default contract. `--trust-embedded-key` accepts the sender's self-declared
  public key on first use; use it only when you have another channel to confirm
  the fingerprint.
- **The private key never leaves the sender.** Ed25519 signing is the default;
  `--secret` offers symmetric HMAC for operators who cannot manage key pairs,
  at the cost of shared trust. Prefer Ed25519.
- **No transport is implied.** An envelope is a file. Moving it is the
  operator's choice; LEVH opens no socket for it.

## Legacy environment names

`STACKMEMORY_*` variables (for example `STACKMEMORY_TOKEN`) are accepted for
backward compatibility but are deprecated. Use the `LEVH_*` spelling — the
legacy names will be removed in a future release.
