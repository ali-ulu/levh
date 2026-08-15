# CLAUDE.md

Guidance for Claude Code and other AI agents working in this repository.

## Hata disiplini

`LESSONS.md` dosyasını her görev öncesi kontrol et, her hata sonrası güncelle. Detay: hata-disiplini skill.

- Göreve başlamadan önce `LESSONS.md` içinde görev/modül adıyla eşleşen anahtar kelimeyi ara ve `## KALICI KURALLAR` bölümünü tara.
- Bir hata düzelttikten sonra RCA özetini (`HATA` / `KÖK NEDEN` / `KURAL` / `KAPSAM`) `LESSONS.md`'ye ekle.

`LESSONS.md` repoda **takip edilmez** — çalışma günlüğü niteliğinde ve yereldir
(`.gitignore`'da). Dosya yoksa oluştur; bu bölümdeki iki kural yine geçerlidir.

## What this project is

LEVH is a local-first memory layer for AI agents: a Python MCP server plus a
Next.js dashboard, all persisted to SQLite. No cloud services, no accounts.

- `server/` — MCP server, CLI, memory engine, FastAPI app
- `server/api.py` + `server/routes/` — the app, then one module per URL prefix
- `server/cli.py` + `server/cli_parsers.py` + `server/commands/` — dispatch,
  parsers, implementations
- `server/core/memory_engine.py` + `server/core/engine/` — the class, then its
  behaviour as mixins by responsibility
- `server/core/database.py` + `server/core/db/` — connection and migrations,
  then query groups per table
- `server/tools/` — one module per MCP tool/resource, wired up in `register.py`
  and tiered in `profiles.py`
- `server/dashboard/` — packaged dashboard, generated from `frontend/out/`
- `frontend/` — Next.js source; `frontend/out/` is a committed build artifact
- `tests/` — pytest suite, must run offline
- `scripts/release.py` — version bump, frontend build, dashboard sync;
  pushing a `v*` tag then publishes via `.github/workflows/publish.yml`
  (see `docs/releasing.md`)

## Ground rules

See CONTRIBUTING.md for the full list. The ones that bite most often:

- Keep changes narrow and testable. Fix the reported bug, not the surrounding code.
- Tests must run without an OpenAI key and without `sentence-transformers`.
  Use `EMBEDDER_MODE=hash` for anything deterministic.
- Do not commit runtime artifacts (`.env`, `*.db`, `.pytest_cache`, `.next`,
  `node_modules`, logs, generated exports).
- Do not rebuild `frontend/out/` as a side effect of unrelated work —
  `scripts/release.py` regenerates and syncs it at release time.

## Validation

```bash
python -m pip install -e ".[dev]"
python -m compileall -q server tests
EMBEDDER_MODE=hash python -m pytest -q
```

```bash
cd frontend
npm ci
NEXT_TELEMETRY_DISABLED=1 npm run build
npm audit --omit=dev
```

CI runs exactly these across Python 3.11/3.12/3.13. Run the full suite before
pushing — several tests assert on repository files (workflow YAML, lockfile,
version badges), so a change to CI config or dependencies can fail a test that
looks unrelated to the code you touched.

## Adding a CLI subcommand

`server/cli.py` keeps the parsers and the dispatch chain far apart. Add both,
then confirm you did not displace a neighbouring parser:

```bash
python -m server.cli <new-command> --help
python -m server.cli --help   # every pre-existing command still listed
```

## Adding an MCP tool or resource

Add the module under `server/tools/`, register it in `server/tools/register.py`,
give it a tier in `server/tools/profiles.py` (a test locks the tier map to the
registry, and the hardcoded tool counts in `profiles.py`, `configs.py` and the
docs move with it),
and read the resource back before calling it done — registration succeeding does
not mean the URI resolves:

```python
await mcp.read_resource("levh://...")
```

Resource template parameters must be path segments. A query-string template
(`.../thing?task={task}`) registers cleanly and then never matches, because
FastMCP does not escape `?` when it compiles templates to regexes.

## Docs that are locked to the code

`docs/api-reference.md`, `docs/cli.md`, `docs/mcp-tools.md` and
`docs/mcp-client-config.md` are checked against the running app by
`tests/test_docs_match_code.py`. A new route, subcommand, tool or client fails
the suite until the table lists it.

## Splitting a file

Do not copy constants into the new module. `scripts/release.py` rewrites only
the version sites it knows about, so a duplicated version literal silently
stays behind — run `python scripts/release.py --check` after any split that
touches a file holding one.
