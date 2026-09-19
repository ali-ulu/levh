# Contributing

LEVH is currently focused on release hardening and reliable local-first
installation. That focus shapes what a good contribution looks like: it has to
work offline, without a remote model call, and without asking the user to trust
a service they did not choose.

## Before you start

- **Bugs and small fixes** — open a pull request directly. The issue and pull
  request templates explain what a reviewer needs to see.
- **Features** — open a feature request first so the design is agreed before
  code exists. Cloud, auth, billing, and workspace features need a design issue
  by policy; a pull request that adds them without one will be closed.
- **Security issues** — do not open a public issue. Follow `SECURITY.md`.

## Ground rules

- Keep changes narrow and testable.
- Do not add cloud, auth, billing, or workspace features without a design issue.
- Do not commit runtime artifacts such as `.env`, `stackmemory.db`, `.pytest_cache`, `.next`, `node_modules`, logs, or generated exports.
- Keep tests runnable without an OpenAI key and without `sentence-transformers`.
- Use `EMBEDDER_MODE=hash` for deterministic CI and smoke tests.
- Do not reformat code you did not otherwise change. This repository predates
  ruff and keeps its historical style: `.ruff.toml` selects correctness rules
  only, deliberately.

## Development setup

The Python dependency graph is locked in `uv.lock` (issue #146). Install from
the lock so your environment matches CI and every release:

```bash
uv sync --frozen --extra dev
```

This is what CI runs in every job. `--frozen` refuses to re-resolve, so a
`pyproject.toml` change that is not committed as a regenerated lock fails
loudly instead of silently building a different graph. After editing a
dependency in `pyproject.toml`:

```bash
uv lock          # update uv.lock
uv sync --frozen --extra dev
```

`uv` also provides the dev tools (pytest, ruff, mypy); prefix local commands
with `uv run --frozen` or activate the environment. If you prefer plain pip,
`python -m pip install -e ".[dev]"` still works, but it installs from
pyproject's floor pins and may differ from the locked graph CI uses.

The optional extras — `.[local]` for the real embedder, `.[files]` for
PDF/Word/Excel import, `.[pdf]` for the audit report — are not needed for
development.

### Pre-commit (recommended)

```bash
python -m pip install pre-commit
pre-commit install
```

This runs the same ruff version CI installs, on every commit, so a lint failure
does not wait for CI to surface. See `.pre-commit-config.yaml`.

## Local validation

Run these before opening a pull request. They are the gates CI enforces.

```bash
python -m pip install -e ".[dev]"
python -m compileall -q server tests
EMBEDDER_MODE=hash python -m pytest -q
EMBEDDER_MODE=hash python -m pytest -q tests/test_api_smoke.py
python -m ruff check .
python -m mypy
python -m build
twine check dist/*
```

Frontend validation (required if you touched `frontend/`):

```bash
cd frontend
npm ci
NEXT_TELEMETRY_DISABLED=1 npm run build
npm audit --omit=dev
```

## What happens after you open a pull request

CI runs seven gates — `lint`, `types` (mypy over the annotated tier listed in
`[tool.mypy].files`; grow that list when you fix a module), `backend` (Python
3.11/3.12/3.13), `hostile-env` (the suite against a decoy database, to catch
tests that would write to a real store), `pip-audit`, `coverage`, and
`frontend`. The `coverage` gate lives inside the `backend` job: it enforces a
72% total floor and, on pull requests only, 70% on the changed lines; see
[Testing → Coverage gates](docs/testing.md#coverage-gates). A pull request is
merged only when every one of them is green; a pending or missing check is not
a green check.

A change to `.github/workflows/`, `server/core/engine/`, `server/api.py`,
`server/routes/`, or `pyproject.toml` also requests review from `CODEOWNERS`.

## Commit messages

Follow the existing history: a type prefix, a lowercase summary, and the issue
number in parentheses — for example `fix: debounce write-triggered rebuild
retries (#166)`. The `feat` / `fix` / `docs` / `refactor` / `test` / `chore`
prefixes are what the changelog is generated from.
