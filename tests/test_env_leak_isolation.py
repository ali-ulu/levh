"""The suite must never reach the developer's real memory store.

On 2026-09-13 a machine-level ``LEVH_SQLITE_DB_PATH`` turned 28 subprocess
tests into writers of the real database: ``server.core.env.get_env`` prefers
the ``LEVH_``-prefixed name over the plain ``SQLITE_DB_PATH`` the suite sets,
and CI — where the variable does not exist — stayed green. Fourteen fixture
memories, five fake guard violations, fourteen attachments and the whole
entity graph landed in production memory before anyone noticed.

Scrubbing those names was only half of it. With none set, resolution falls
back to ``<cwd>/stackmemory.db`` — the workspace's real store — so tests that
built an engine without naming a path still wrote into it, and the next run
read those rows back as duplicates. ``tests/conftest.py`` now *pins* the store
per test (a temp database under that test's ``tmp_path``) and scrubs the
spellings that could outrank the pin. These tests hold it to that:

* the suite's own environment must never name a store inside the workspace,
* a child process inheriting the suite's environment must resolve the pinned
  store rather than the workspace default, even from the workspace as cwd.

On a hostile machine (the CI ``hostile-env`` job plants one) the scrub is what
keeps both true.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Must match tests/conftest.py. get_env() accepts plain, LEVH_-prefixed and
# legacy STACKMEMORY_ spellings, and LEVH_CONFIG_PATH redirects config
# resolution — all four can end with the suite reading someone's real data.
STEERING_NAMES = (
    "LEVH_SQLITE_DB_PATH",
    "SQLITE_DB_PATH",
    "STACKMEMORY_SQLITE_DB_PATH",
    "LEVH_CONFIG_PATH",
)

# The one name the suite sets itself; the other three must be scrubbed, since
# each of them outranks it.
PINNED_NAME = "SQLITE_DB_PATH"


def test_the_suite_pins_the_store_outside_the_workspace():
    for name in STEERING_NAMES:
        if name == PINNED_NAME:
            continue
        assert name not in os.environ, (
            f"{name} outranks the store the suite pins; tests/conftest.py "
            "scrub failed and subprocess tests may read or write the "
            "developer's real memory"
        )

    pinned = os.environ.get(PINNED_NAME, "")
    assert pinned, (
        f"tests/conftest.py must pin {PINNED_NAME} for every test: with none "
        "of the steering names set, resolution falls back to "
        "<cwd>/stackmemory.db, which is the workspace's real store"
    )
    resolved = Path(pinned).resolve()
    assert REPO_ROOT not in resolved.parents, (
        f"the suite points the store at {resolved}, inside the workspace; the "
        "real store must be unreachable from a test"
    )


def test_a_subprocess_inheriting_the_suite_environment_stays_isolated():
    """cwd is the workspace, so the pinned store is the only thing keeping this
    child away from the real database."""
    probe = "from server.core.runtime_config import resolve_runtime_config; print(resolve_runtime_config().database_path)"

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.strip()).resolve()

    assert resolved != (REPO_ROOT / "stackmemory.db").resolve(), (
        "a child process inheriting the suite's environment resolved the "
        "workspace store; this is how the 2026-09-13 leak happened"
    )
    assert REPO_ROOT not in resolved.parents


def test_an_engine_built_without_a_path_uses_the_pinned_store():
    """The leak's real entry point: an engine that nobody gave a path.

    This is what routes, the CLI and every helper fall back to. Before the
    pin, it resolved to ``<cwd>/stackmemory.db`` and wrote there.
    """
    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine()
    resolved = Path(engine.db.db_path).resolve()

    assert resolved == Path(os.environ[PINNED_NAME]).resolve(), (
        "an engine built without a path did not land in the store the suite "
        "pinned; it may be writing to the workspace's real memory"
    )
    assert REPO_ROOT not in resolved.parents


def test_a_test_may_still_name_its_own_store(monkeypatch, tmp_path):
    """The pin is a default, not a hijack: an explicit path still wins."""
    from server.core.memory_engine import MemoryEngine

    own = tmp_path / "own.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(own))

    assert Path(MemoryEngine().db.db_path).resolve() == own.resolve()


def test_a_first_test_caches_an_engine(monkeypatch, tmp_path):
    """First half of a pair: cache an engine the way a route does.

    Deliberately leaves it behind — ``tests/conftest.py`` owns the cleanup.
    """
    from server import api
    from server.core import engine_provider
    from server.core.memory_engine import MemoryEngine

    engine = MemoryEngine()
    api._engine = engine
    engine_provider.set_engine(engine)
    api.app.state.engine = engine


def test_the_next_test_starts_free_of_the_previous_engine():
    """Second half: a cached engine must not survive a test boundary.

    A surviving engine keeps the previous test's database *and* its in-memory
    vector store, so the admission gate reads that test's rows as this test's
    duplicates. Under a randomized order this can run before the test that
    caches the engine; the invariant is unconditional, so it still holds.
    """
    from server import api
    from server.core import engine_provider

    assert api._engine is None
    assert engine_provider._engine is None
    assert getattr(api.app.state, "engine", None) is None


def test_a_subprocess_resolves_the_store_the_suite_points_at(tmp_path):
    db = tmp_path / "isolated.db"
    env = {k: v for k, v in os.environ.items() if k not in STEERING_NAMES}
    env["SQLITE_DB_PATH"] = str(db)
    env["EMBEDDER_MODE"] = "hash"

    first = subprocess.run(
        [sys.executable, "-m", "server.cli", "setup", "--real", "--client", "claude", "--profile", "work"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert first.returncode == 0, first.stderr

    status = subprocess.run(
        [sys.executable, "-m", "server.cli", "setup", "--status"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert status.returncode == 0, status.stderr
    data = json.loads(status.stdout)
    assert data["memory_count"] == 0, (
        "a subprocess resolved a store other than the one the suite pointed "
        f"it at ({db}); this is how the 2026-09-13 leak happened"
    )
    assert db.exists(), "the isolated database was never created"
