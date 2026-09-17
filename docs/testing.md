# Testing

```bash
pip install -e ".[dev]"
EMBEDDER_MODE=hash python -m pytest -q
```

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

