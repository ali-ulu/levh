# Testing

## The development environment

The suite is known to run in exactly one environment: the one `uv` builds from
`uv.lock`, from the repository root. Two commands, in this order:

```bash
uv sync --frozen --extra dev
EMBEDDER_MODE=hash uv run --frozen python -m pytest -q
```

The order is the part that gets missed. `--extra dev` is not optional: `pytest`
is a *dev* dependency, so an environment synced without the extra has no test
runner in it at all. `--frozen` refuses to re-resolve, so the graph you test is
the graph CI tests. On Windows the interpreter can be run directly, which also
removes any doubt about which `python` on `PATH` is being used:

```powershell
$env:EMBEDDER_MODE = "hash"
.venv\Scripts\python.exe -m pytest -q
```

`EMBEDDER_MODE=hash` is the POSIX shell form of the same setting; set it in
whatever way your shell exports a variable.

The Python graph is locked in `uv.lock` (issue #146); CI installs from it in
every job with `uv sync --frozen --extra dev`. Plain
`pip install -e ".[dev]"` still runs the suite but installs from pyproject's
floor pins instead of the locked graph.

### Six traps that look like code failures

Each of the six below is an environment problem, and each one surfaces in a
shape that gets read as "the change broke something". Check the environment
before reading a diff.

#### 1. An interpreter outside the lock

**Symptom.** The run dies before collecting anything, at import time, with an
error about the two halves of pydantic disagreeing:

```text
SystemError: The installed pydantic-core version is incompatible with the
installed pydantic version
```

It reads as flaky because it is not reproducible from the repository's point of
view: the same commit imports cleanly on one machine and raises on another, and
a run that passed can fail on the next invocation with no source change between
the two.

**Cause.** A `python` that is not the environment `uv` built. Its site-packages
pair a `pydantic` release with a `pydantic_core` built for a different one
(`pydantic` 2.13.5 needs `pydantic-core` 2.46.5, not 2.41.5), and the mismatch
is raised during import — before a line of this project runs. Nothing in the
checkout can explain it, which is exactly why it is misread as a code failure.

**Fix.** Test through `.venv` or `uv run`, never through a bare `python`. When a
failure appears with no cause in the diff, ask which interpreter produced it:

```bash
python -c "import sys; print(sys.executable)"
```

#### 2. `uv` synced without the `dev` extra

**Symptom.**

```text
No module named pytest
```

**Cause.** `--frozen` selects the locked graph; it does not select extras. A
`uv sync --frozen` without `--extra dev` installs the runtime dependencies and
removes the dev tools, so `.venv\Scripts\python.exe -m pytest` and
`uv run --frozen python -m pytest` then fail identically, and re-running
`uv run --frozen` does not repair it — it syncs the same extra-less environment
again.

**Fix.** Name the extra on the sync, and on any `uv run` that starts from a
fresh environment:

```bash
uv sync --frozen --extra dev
uv run --frozen --extra dev python -m pytest -q
```

#### 3. `uv` invoked from outside the project

**Symptom.** Two different messages, one cause. `uv sync` from a directory that
is not inside the checkout stops with:

```text
error: No `pyproject.toml` found in current directory or any parent directory
```

`uv run --frozen python -m pytest` from the same directory does not stop: it
finds no project, warns that `--extra dev` "has no effect when used outside of a
project", and runs a *managed* interpreter instead — so the failure is reported
as `No module named pytest` and looks like trap 2.

**Cause.** `uv` resolves the project from the working directory. Called from a
script directory, a home directory, or a second checkout, it either stops or —
worse, for `uv run` — quietly tests an environment that is not this project's.

**Fix.** Run from the repository root, or name the project explicitly with
`uv run --frozen --project <path-to-repo>`.

#### 4. `uv`'s cache under a restricted session

**Symptom.** Every `uv` invocation dies before doing anything:

```text
error: Failed to initialize cache at `C:\Users\<user>\AppData\Local\uv\cache`
  Caused by: failed to open file `...\sdists-v9\.git`: Erişim engellendi. (os error 5)
```

It reads as a broken cache and invites deleting it. The cache directory is
outside what the session may write: an agent session running under a sandbox
scoped to one workspace cannot touch `AppData`, and every `uv` command —
including a read-only-looking `uv run` — initialises that cache first.

**Fix.** Redirect the cache to a directory the session may write (a platform
temp area is enough), and set it in the *same* invocation — a fresh process
loses the variable, so a command that worked five minutes ago fails again for
no code reason:

```powershell
$env:UV_CACHE_DIR = Join-Path $env:TEMP 'levh-uv-cache'; uv run --frozen ruff check .
```

`--frozen` needs no network to re-resolve, but `uv` itself still wants its
cache; a redirected one is created fresh and reused from then on.

#### 5. pytest's temp directories under a low-integrity session

**Symptom.** Every test errors at setup — thousands of `ERROR` lines, zero
failures — and the same `PermissionError` also surfaces at session finish:

```text
PermissionError: [WinError 5] Erişim engellendi: 'C:\...\pytest-of-<user>'
```

It reads as the suite being broken. It is not a test failure: not one test ran.

**Cause.** pytest's tmp machinery creates every directory with
`mkdir(mode=0o700)` (`_pytest/tmpdir.py`, `getbasetemp` and
`make_numbered_dir`), and the session's token is low-integrity. On this setup a
directory created that way denies *every* subsequent access — including to the
process that created it and to `icacls` — so the next `scandir` inside the
factory raises. The trap is expensive to isolate because the plain probes lie:
`os.makedirs(p)` followed by `os.listdir(p)` works, so the sandbox looks
innocent.

**Fix.** Run the suite with the sandbox lifted (the session's full-access
mode). A `--basetemp` in a writable directory does *not* help: the trigger is
the `mode=0o700` mkdir itself, not the location. The two-line reproduction,
when in doubt:

```python
os.mkdir(p, 0o700)   # then: os.scandir(p) -> WinError 5
os.mkdir(p)          # default mode: works
```

#### 6. A live server darkening the doctor tests

**Symptom.** `tests/test_remote_access_boundary.py` and the doctor case in
`tests/test_operational_hardening.py` fail one run and pass the next, with no
source change between the two — or fail steadily on one machine while CI stays
green.

**Cause.** The tests assert the argv/config path by pointing the configured
port at silence (`API_PORT=1`), but `levh doctor`'s live probe
(`server/commands/doctor.py`) also tries the 8000/9000 fallbacks. A server
left running from a debug session — or started at logon, as `levh_serve.bat`
is on this machine — answers there, and the check trusts the serving process
over argv. The verdict then depends on ambient port state, not on the change
under test. This is the doctor variant of the Playwright trap in `AGENTS.md`
("a server you left running from a debug session is silently reused").

**Fix.** Tests asserting the no-server path set `LEVH_DOCTOR_NO_LIVE_PROBE=1`,
which makes the probe return `None` so argv and config decide. Tests that
exercise the probe itself
(`test_doctor_prefers_what_a_live_server_reports`,
`test_doctor_probes_the_port_from_argv`) spin their own server on an
ephemeral port and leave the variable unset.

## What the suite covers

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

### Recall quality gate

`levh benchmark --check` exits non-zero when a gated metric falls below its
floor, and CI runs it on Python 3.12 (`.github/workflows/ci.yml`, step "Recall
quality gate"). Unit tests can all pass while ranking quality silently drops —
a weight tweak or a candidate-source change moves hit@k without changing any
assertion — so this is the only thing watching the numbers themselves.

The floors live in `server/core/benchmark.py::QUALITY_FLOORS` and only the
model-free (`hash`) mode is gated: it is deterministic (no model, no network,
no platform-specific arithmetic), so the same corpus yields the same numbers on
every runner. A semantic mode's numbers depend on the installed model and are
not gated.

The corpus is deliberately not trivial: alongside exact-match queries it mixes
paraphrases that share no content word with the stored memory (reaching it only
through the synonym layer or the language-agnostic stem) and near-miss
distractors that share a query's vocabulary but answer a different question. So
the floors sit below 1.0 and a weight tweak or a candidate-source change moves
them — the gate can actually observe a regression. Raising the corpus
difficulty means editing the floors in the same commit, which keeps the change
deliberate and reviewable instead of silently erasing the signal.

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
same way it neutralises the LLM variables. Behaviour flags that steer the
suite without naming a path are scrubbed too: `LEVH_RECALL_LOG` and
`LEVH_RECALL_LOG_DAYS` decide whether recalls are recorded, so a developer who
exports them to exercise the recall log — now usually to turn it *off* — would
otherwise redden `tests/test_recall_log.py` while CI stayed green.

This is not paranoia: on 2026-09-13 a developer-level `LEVH_SQLITE_DB_PATH`
made 28 subprocess tests write fixture rows into the *real* memory database
while CI stayed green, because Actions machines have no such variable.
`server.core.env.get_env()` prefers the `LEVH_`-prefixed spelling over the
plain one the tests set, so the isolation promise silently inverted.

CI runs the same defense as a job: **hostile-env** plants decoy variables
pointing at a canary database, and decoy behaviour flags, then runs the full
suite against them. If any test still reaches the real store, the canary fills
up and the job fails — the leak is caught in CI instead of on someone's
production memory. The contract is pinned by
`tests/test_env_leak_isolation.py`; keep that file and the conftest list in
sync when a new steering variable or behaviour flag is introduced.

