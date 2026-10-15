# LEVH — Getting Started

LEVH is local-first. The database, onboarding receipt, and optional dogfood
journal stay on the machine unless the user explicitly exports a file.

## First run

```bash
pip install levh
levh setup --client claude --profile work
levh capture "Atlas uses PostgreSQL in production."
levh serve
```

Open `http://localhost:8000`. The setup command prepares an empty local store,
generates a focused Claude MCP config under `.stackmemory/mcp/`, and writes a
privacy-safe local setup receipt. It never seeds data and never overwrites an
existing store.

`capture` uses the normal memory store path; it does not silently pin or boost
trust.

## Readiness and diagnostics

```bash
levh setup --status
levh doctor
```

A zero-memory database is a first-run warning, not an installation failure.
`doctor` reports database writability, embedder behavior, dashboard packaging,
MCP profile counts, dogfood state, journal discovery, memory count, and the
recommended next action.

## MCP profiles

Generated configs default to `work` rather than advertising all 74 tools.

```bash
levh setup --client cursor --profile minimal
levh mcp profiles
```

Profiles reduce the tool-discovery surface. They are **not** authentication,
authorization, or a security boundary.

## Optional local dogfood metrics

Dogfood measurement is disabled by default. To collect whitelisted local usage
events for a process:

```bash
LEVH_DOGFOOD_ENABLED=true levh serve
```

Raw memory and query content are not recorded, and the journal performs no
network I/O. A process started without the flag must be restarted. Historical
journal files are not migrated automatically.

Journal path precedence is:

1. CLI `--journal`
2. `DOGFOOD_JOURNAL_PATH`
3. next to `SQLITE_DB_PATH`
4. `./dogfood_events.jsonl`

```bash
levh dogfood status
levh dogfood export --output report.json
```

Export is an explicit action and contains aggregates only.
