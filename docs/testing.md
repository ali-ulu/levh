# Testing

```bash
uv sync --frozen --extra dev
EMBEDDER_MODE=hash uv run --frozen python -m pytest -q
```

The Python graph is locked in `uv.lock` (issue #146); CI installs from it in
every job with `uv sync --frozen --extra dev`. Plain
`pip install -e ".[dev]"` still runs the suite but installs from pyproject's
floor pins instead of the locked graph.

The suite covers memory lifecycle, H(x,ψ) scoring, adaptive decay/reinforcement,
outcome feedback, retroactive interference, fading review queue, forgetting curves,
sessions, consolidation, export/import, concurrent operations, edge cases, session
isolation, project namespacing, source tracking, pinning,
recall correctness (no side effects on
non-returned candidates), env-configurable weights, mixed embedding dimensions,
v1 → v2 schema migration, dedupe, context file generation, related memories,
session summarization, the recall-quality benchmark harness, and the REST API.

Benchmark recall quality directly with `levh benchmark`. Source-tree users
can also run `python scripts/benchmark_recall.py`; the runtime implementation is
packaged under `server.core.benchmark` so wheel installs do not depend on the
non-package `scripts/` directory. Run with `EMBEDDER_MODE=local`, `ollama`, or
`openai` for a meaningful semantic signal; the default `hash` embedder is
non-semantic and intended for deterministic smoke checks.

## Coverage gates

CI enforces two numeric coverage gates, both only on Python 3.12 (the
`backend` matrix runs the plain suite on 3.11 and 3.13):

- **Total floor — 72%.** `pytest --cov=server --cov-fail-under=72` applies to
  the whole `server/` package on every run. The baseline measured 2026-09-18
  was 73.2% total, so the gate sits about a point under the measured floor:
  refactors can shuffle lines without turning a green build red, but a module
  that silently loses its tests fails loudly.
- **Changed lines — 70%.** On pull requests only, `diff-cover` re-checks just
  the lines the diff touches:

  ```bash
  diff-cover coverage.xml --compare-branch "origin/<base>" --fail-under 70
  ```

  The total floor is a floor: a PR could add a whole untested module and still
  clear it. This second gate closes that gap (issue #206).

Both thresholds live in one place — `--cov-fail-under` and `--fail-under` are
passed on the command line in `.github/workflows/ci.yml`; `fail_under` is
mirrored in `[tool.coverage.report]` because `--cov-fail-under` overrides it.
Change the number in CI and change it there too.

`diff-cover` needs a merge-base with the base branch, which a shallow checkout
cannot provide, so the `backend` job checks out the full history
(`fetch-depth: 0`). The gate is skipped on pushes to `main`: after the squash
merge there is no PR diff left to compare against.

Reproduce the gates locally before opening a PR:

```bash
EMBEDDER_MODE=hash python -m pytest -q --cov=server --cov-report=xml:coverage.xml \
  --cov-fail-under=72
diff-cover coverage.xml --compare-branch origin/main --fail-under 70
```

`tests/test_docs_match_code.py` pins both thresholds to this page, so moving a
number without updating the docs turns the build red.

## The suite owns its environment

Subprocess-based tests spawn the real CLI/server/MCP entry points, so any
variable in the surrounding environment can quietly redirect them at the real
memory store. `tests/conftest.py` therefore scrubs every name that can steer
the suite — `LEVH_SQLITE_DB_PATH`, `SQLITE_DB_PATH`,
`STACKMEMORY_SQLITE_DB_PATH` and `LEVH_CONFIG_PATH` — before each test, the
same way it neutralises the LLM variables.

This is not paranoia: on 2026-09-13 a developer-level `LEVH_SQLITE_DB_PATH`
made 28 subprocess tests write fixture rows into the *real* memory database
while CI stayed green, because Actions machines have no such variable.
`server.core.env.get_env()` prefers the `LEVH_`-prefixed spelling over the
plain one the tests set, so the isolation promise silently inverted.

CI runs the same defense as a job: **hostile-env** plants decoy variables
pointing at a canary database and runs the full suite against it. If any test
still reaches the real store, the canary fills up and the job fails — the
leak is caught in CI instead of on someone's production memory. The contract
is pinned by `tests/test_env_leak_isolation.py`; keep that file and the
conftest list in sync when a new steering variable is introduced.

