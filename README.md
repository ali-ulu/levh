<p align="center">
  <strong>LEVH</strong><br>
  Local-first memory layer for AI agents and humans<br>
  <em>Memory that forgets like you do — unless it matters.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/levh/"><img src="https://img.shields.io/pypi/v/levh?logo=pypi&logoColor=white" alt="PyPI version"></a>
  <img src="https://img.shields.io/badge/MCP-Protocol-blue?logo=anthropic" alt="MCP">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-AGPL--3.0--or--later-green" alt="License">
  <a href="https://github.com/ali-ulu/levh#readme"><img src="https://img.shields.io/badge/docs-README-0D1117?logo=readme&logoColor=white" alt="LEVH documentation"></a>
</p>

---

## What is LEVH?

Your AI tools are stateless — every session starts from zero. LEVH gives them a
persistent, searchable memory that lives on your machine. Plug it into Claude
Desktop, Cursor, Claude Code, VS Code, or any MCP client, and it remembers your
decisions, projects and people across sessions.

Most memory tools store everything and retrieve everything. **LEVH forgets on
purpose:** every memory has its own decay curve, unused ones fade, and the ones
you actually rely on are reinforced automatically. Signal rises to the top
without manual curation.

Everything runs locally on SQLite. No accounts, no cloud, no external services.

## Quick start

```bash
pip install levh
levh setup --client claude --profile work
levh capture "Atlas uses PostgreSQL in production."
levh serve
```

Dashboard and API come up on <http://localhost:8000>. Open **Settings** for
copy-paste MCP configs, or run `levh mcp config cursor` for any supported client.

Optional, once you are set up:

```bash
levh hook install --client claude-code   # every new session starts with your memory
levh hook install                        # capture every git commit message
levh context -o CLAUDE.md                # compile memory into a context file
```

## How it works

Every memory has its own half-life. Recalling it resets the clock and grows it,
negative feedback shrinks it, and pinning skips decay entirely.

```
retention(t) = 0.5 ^ (hours_since_last_recall / stability_hours)
```

Memories are ranked by an explainable multi-factor score — semantic similarity,
decay, importance and access frequency, each weight configurable. See
[Architecture](docs/ARCHITECTURE.md).

## What you get

- **Adaptive decay** — per-memory half-life, reinforced by recall and weakened by feedback.
- **Ask your memory** — natural-language answers that cite the memories they came from. Offline by default.
- **People, projects & timeline** — entities extracted from your memories automatically. No manual tagging.
- **Daily briefing & meeting prep** — today's events and who you are about to meet.
- **Decisions & conflicts** — decisions pulled from your memories, with a review signal when two disagree. A signal, never a verdict.
- **Knowledge Wiki** — a source-linked Markdown wiki built from your memories, browsable in the dashboard.
- **Admission gate** — every memory screened before storage; duplicates flagged and secrets redacted. An ambiguous candidate is held for you, never dropped.
- **Trust & provenance** — an explainable reliability score per memory. Separate from ranking; not a truth claim.
- **Mistake guard** — a corrected mistake becomes a pinned rule that never decays.
- **Encrypted backup & restore** — one portable snapshot, optionally encrypted.
- **Connectors** — Calendar, Email, transcripts, Notion, Obsidian, GitHub, Jira, Linear, Slack, git history and local files.
- **Signed federation** — explicit, pull-first memory exchange between two LEVH instances.
- **78 MCP tools**, a REST API, a WebSocket feed, and a Next.js dashboard served by the API itself — one process, one port.

## Documentation

| | |
|---|---|
| [Getting Started](docs/getting-started.md) | First run and local setup |
| [Platform Setup](docs/mcp-client-config.md) | Claude, Cursor, VS Code, Codex and the rest |
| [MCP Tools](docs/mcp-tools.md) | All 78 tools and the profile bands |
| [REST API](docs/api-reference.md) | Every endpoint |
| [CLI](docs/cli.md) | Every command |
| [Knowledge Wiki](docs/llm-wiki.md) | The source-linked wiki over your memories |
| [Architecture](docs/ARCHITECTURE.md) | Layers, engine, scoring internals |
| [Releasing](docs/releasing.md) | Version bump, tag, automated publish |

→ [Installing from source](docs/installation.md) · [Configuration](docs/configuration.md)

## Security

LEVH is a local, single-user tool — no accounts, no multi-tenancy.

- **Tokenless means loopback-only.** Without `LEVH_TOKEN`, remote peers are rejected.
- **`LEVH_TOKEN`** gates `/api/*`, the WebSocket and the MCP SSE transport. Set it before widening any bind.
- **Nothing leaves the machine without an explicit opt-in.** Ask, summaries and transcript ingest stay offline until you set `ANSWER_MODE=llm` or `SUMMARY_MODE=llm`.
- **Secrets are redacted** before storage, on every write path.

The MCP size profiles narrow tool *discovery*; `read_only` is enforced
server-side at call time. The size profiles are not a security boundary.

Found a vulnerability? See [SECURITY.md](SECURITY.md).

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
and [Discussions](https://github.com/ali-ulu/levh/discussions).

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).
