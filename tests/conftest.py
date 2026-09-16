"""Shared test setup.

The suite must describe its own environment. It did not, in three ways.

Several tests assert what happens with *no* LLM configuration but read the
developer's real environment, so on a machine with OPENAI_BASE_URL pointed at
OpenRouter and SUMMARY_MODE set they failed — while passing in CI and on a
clean checkout. That is the worst failure mode a test can have: red for a
reason that has nothing to do with the change under test, which trains
everyone to ignore it. Clearing those variables makes "unset" the baseline; a
test that wants a value still sets it with monkeypatch, which runs after this
fixture and wins.

The librarian watcher is the other one. It starts with the app and writes into
whatever store the app is using — a row no test asked for, arriving from a
background thread at an unpredictable moment. Tests that exercise the watcher
turn it on themselves.

It happened a third way, on 2026-09-13: the developer's machine exported
``LEVH_SQLITE_DB_PATH`` pointing at the real memory store, and
``server.core.env.get_env`` prefers the ``LEVH_``-prefixed name over the plain
one the suite sets. Subprocess-based tests therefore ignored their own
``SQLITE_DB_PATH`` and wrote fixture rows into the *real* memory. CI never saw
it because Actions machines have no such variable — the failure was invisible
exactly where the suite is trusted most.

Scrubbing those names fixed the hostile machine but not the default: with
nothing set, ``resolve_runtime_config`` falls back to ``<cwd>/stackmemory.db``,
which in a developer checkout *is* the real store. Any test that built an
engine without naming a path — directly, or through an app whose routes build
one — wrote fixture rows there, and the next run in the same directory read
them back as duplicates (``stored: 0``) instead of a clean database. So the
suite no longer merely avoids steering variables: it pins the store itself,
one temp database per test, and keeps the spellings that could outrank it
scrubbed. CI's ``hostile-env`` job pins the scrub end to end.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from server.core.env import accepted_env_var_names
from server.core.runtime_config import CONFIG_PATH_ENV

os.environ["LEVH_LIBRARIAN"] = "0"

# Read by server.core.llm_endpoint, llm_policy and summarizer. Anything here
# changes whether a call goes out, where it goes, and with which model.
_LLM_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "SUMMARY_MODE",
    "SUMMARY_MODEL",
)

# The plain database-path name the suite pins.
_PINNED_ENV = "SQLITE_DB_PATH"
_PINNED_BASE_NAMES = (_PINNED_ENV,)

# Every name through which a developer's environment could redirect the suite
# (or the CLI/server/MCP subprocesses it spawns) at the real memory store.
# Derived from accepted_env_var_names — the same acceptance rules get_env
# implements — so adding a new accepted spelling to get_env extends this list
# automatically instead of silently bypassing the pin. Each spelling of the
# database path outranks the plain one the suite pins, so all of them go,
# along with every spelling of the config-path redirect.
_STEERING_ENV = tuple(
    spelling
    for base in (*_PINNED_BASE_NAMES, CONFIG_PATH_ENV)
    for spelling in accepted_env_var_names(base)
)

# The file each test's pin points at, inside the test's tmp_path.
_ISOLATED_STORE_NAME = "isolated.db"


@pytest.fixture(autouse=True)
def _neutral_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolated_memory_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Pin this test's database to a temp store it owns, then forget the engine.

    One place decides where the suite writes: this fixture. It scrubs the
    spellings that could outrank the pin (a hostile machine, or a test that
    ran before this one), points ``SQLITE_DB_PATH`` at a temp file under this
    test's ``tmp_path``, and on the way out drops the shared engine so no
    cached connection can carry one test's store into the next.

    A test that wants a store of its own still overrides the variable with
    monkeypatch — that runs later and wins, and its own path is honoured.
    """
    for name in _STEERING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(_PINNED_ENV, str(tmp_path / _ISOLATED_STORE_NAME))
    yield
    _forget_shared_engine()


def _forget_shared_engine() -> None:
    """Drop any engine (and app state) a test left behind.

    A cached engine carried into the next test still holds the previous test's
    database and its in-memory vector store, so the admission gate reads that
    test's rows as this test's duplicates — the same failure the pinned store
    prevents, one level up.

    Nothing is closed here: an app that ran its lifespan closed its own
    engine, and one built implicitly owns a temp file only it can reach.
    Modules are looked up in ``sys.modules`` rather than imported, so a test
    that never touched the API never pays for building the app.
    """
    api = sys.modules.get("server.api")
    if api is not None:
        api._engine = None
        api._initialized = False
        state = getattr(getattr(api, "app", None), "state", None)
        if state is not None and getattr(state, "engine", None) is not None:
            del state.engine

    provider = sys.modules.get("server.core.engine_provider")
    if provider is not None:
        provider.set_engine(None)
