# AGENTS.md

Repository-specific knowledge for agents working in this tree. It records what
is expensive to rediscover, not what the docs already say — see
[`CONTRIBUTING.md`](CONTRIBUTING.md), [`docs/releasing.md`](docs/releasing.md)
and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full picture.

## What this is

LEVH is a local-first memory layer for AI agents: one `MemoryEngine` over
SQLite, exposed over REST, WebSocket, MCP stdio and MCP SSE. Python backend
(`server/`) plus a Next.js static export (`frontend/`) shipped inside the wheel
as `server/dashboard/`.

## When the environment is unclear, ask first

Two of this tree's costliest sessions were spent rediscovering environment
facts one question would have settled in seconds: which repository a fresh
session's working directory actually contains, and why every test errors at
setup under a sandboxed session. A wrong guess recorded as a finding costs more
than the ask. When the repo identity, the interpreter, or a blanket permission
failure does not explain itself in one command, ask — or name the observation
and stop. The environment traps themselves are in
[`docs/testing.md`](docs/testing.md) ("Three traps that look like code
failures", traps 4 and 5). Two agent-tooling traps worth knowing before they
bite:

- Editing a UTF-8 file through a PowerShell redirect or console tooling
  corrupts the content (`â€”` mojibake); use the file-editing tool, not
  shell redirection.
- `gh pr merge` against a strict branch protection reports "not up to date"
  even when the branch is current; merge `main` into the branch, and note that
  auto-merge is off in this repository.

## Validate before opening a pull request

```bash
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen pytest -q
uv run --frozen python scripts/release.py --check
```

- There is no Makefile; `uv` is the entry point. `--frozen` is deliberate:
  a `pyproject.toml` dependency change without a regenerated `uv.lock` must fail
  loudly. Run `uv lock` after editing dependencies.
- `ruff` and `mypy` are pinned in the `dev` extra and must match CI
  (`.pre-commit-config.yaml` pins the same ruff). Do not bump one alone.
- `mypy` runs over a ratchet list in `[tool.mypy].files`. Grow it when you fix
  a module; never remove an entry.
- Tests must pass with `EMBEDDER_MODE=hash` and without an OpenAI key or
  `sentence-transformers`.
- Coverage gates: 72% total floor, and 70% on changed lines on PRs only.

## Release rules

The release is prepared by `scripts/release.py` and published by pushing a tag
(`.github/workflows/publish.yml`). Two things about it are easy to get wrong.

### A released CHANGELOG heading must be exactly `## X.Y.Z`

`publish.yml` extracts the release notes with an **exact-line** match:

```awk
awk -v v="## $VERSION" '$0 == v {found=1; next} …'
```

So a heading with a suffix — a date, a title — never equals `v`. The
extraction yields nothing, the workflow silently falls back to
`See CHANGELOG.md.`, and the GitHub Release ships with no notes while every job
stays green. Every historical heading in `CHANGELOG.md` is dateless for this
reason. `tests/test_repo_process_files.py::test_changelog_heading_matches_the_version_publish_extracts`
reproduces the extraction and fails if the suffix returns.

### Other release invariants

- The version lives in four canonical sites, and `release.py --check` asserts
  all of them agree: `pyproject.toml`, `frontend/package.json`, `server/api.py`,
  and the `X.Y` badge in `frontend/src/components/layout/sidebar.tsx`
  (plus the root of `frontend/package-lock.json`). Bump with the script, not by
  hand.
- The frontend is built **after** the bump so the packaged dashboard can never
  report an older version than the source. Do not reorder that.
- Write the `CHANGELOG.md` section for the version before tagging: the release
  notes are taken from it verbatim.
- The tag must point at a commit whose tree already says that version, or the
  publish build fails on purpose.
- A change that merges without an entry in `## Unreleased` is invisible in the
  release; a test enforces that the section is non-empty.

## Architecture invariants

- All four transports resolve the engine through
  `server/core/engine_provider.get_engine()`. **Never construct a second
  `MemoryEngine` in a request path** — it would split the short-term deque and
  the vector store.
- Behaviour lives in `server/core/engine/` mixins; the class and its
  construction are in `server/core/memory_engine.py`. Database query groups live
  in `server/core/db/`; the DDL is `server/core/db/schema.py`.
- `server/core/db/memories.py::row_to_memory_dict` is the one place the
  storage shape is converted to the model. Both the query layer and the doctor
  check call it so the two cannot drift.
- The store enforces the model's contract on INSERT/UPDATE through triggers
  generated from one rules tuple (`_MEMORY_ROW_RULES`) in the schema module. A
  row the model cannot read back is quarantined at read time, not silently
  dropped.

## Conventions

- Commit messages: type prefix, lowercase summary, issue number in parentheses —
  e.g. `fix: debounce write-triggered rebuild retries (#166)`. The
  `feat`/`fix`/`docs`/`refactor`/`test`/`chore` prefixes feed the changelog.
- Keep changes narrow; this repo predates ruff and keeps its historical style.
  `.ruff.toml` selects correctness rules only — do not reformat untouched code.
- Do not commit runtime artifacts: `.env`, `*.db`, `.next`, `node_modules`,
  logs, generated exports.
- `npm run build` dirties tracked files: `frontend/next-env.d.ts`,
  `frontend/tsconfig.json` and every `frontend/out/**` page. The build is the
  source of truth for the packaged `server/dashboard/`, so the churn is real
  output, not corruption — but it is not part of a feature change. Run
  `git checkout -- frontend/next-env.d.ts frontend/tsconfig.json frontend/out`
  before staging, or the diff carries hundreds of regenerated files.
- Root-level and `docs/*.md` files are checked by
  `tests/test_docs_match_code.py`: relative links must resolve, prose references
  to `tests/<file>.py` must exist, and internal debt inventories belong under
  `docs/internal/`.
- This file is itself a root `*.md`, so any link you add here is validated too.
- The E2E suite runs against one shared SQLite store per server. Two traps:
  (1) with no embedding provider configured, the hash fallback embedder is
  positional, not semantic — model-free recall now ranks on word overlap and the
  admission gate only rejects *exact* duplicates (see
  `tests/test_lexical_recall.py`), so a `409` means the fixture duplicated
  another spec's content byte-for-byte, and `server/core/benchmark.py` is the
  quality signal to watch. Give each spec distinct wording anyway.
  (2) `/api/memories/recall` is a semantic ranking, so the first result for a
  needle is not reliably that needle's own memory; a spec must not assume the
  top hit. Both bit `e2e/palette.spec.ts` in #300.
- Locally, Playwright reuses an already-running server on 8930/8931
  (`reuseExistingServer: !CI`). A server you left running from a debug session
  is silently reused with its stale database, producing flakes that look like
  product bugs. Kill it before a clean E2E run.
