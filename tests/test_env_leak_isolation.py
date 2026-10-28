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

The same trap exists without a path. ``LEVH_RECALL_LOG`` is read at call time
and decides whether recalls are recorded, so a developer who exports ``0`` to
turn the now-default-on log off reddens ``tests/test_recall_log.py`` while CI
stays green. conftest scrubs it too; the behaviour-flag guard below holds that
in place.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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

# The one name the suite sets itself; the others must be scrubbed, since each
# of them outranks it. SCRUB_NAMES is derived from the same source the suite
# derives its scrub list from; the hand copy here is what the drift guard
# tests against.
PINNED_NAME = "SQLITE_DB_PATH"
SCRUB_NAMES = (
    "LEVH_SQLITE_DB_PATH",
    "STACKMEMORY_SQLITE_DB_PATH",
    "LEVH_CONFIG_PATH",
)


def test_conftest_derives_its_scrub_list_from_the_source_of_truth():
    """The scrub list must come from the resolver, not from a hand copy.

    The failure this prevents: the resolver learns a new way to relocate the
    database (a new ``*_path`` setting, or a new spelling ``get_env``
    accepts for an existing one) and a hostile machine sets it — the
    hand-copied scrub list in conftest.py no longer covers it, the pin is
    bypassed, and the suite writes to the developer's real store again.

    Two independent anchors, so neither can drift silently:

    * Bases come from ``runtime_config`` — every setting whose value is a
      path, plus the config redirect — so a new relocatable setting is
      caught even though conftest has never heard of it.
    * What ``get_env`` reads is probed *behaviorally* (sentinel
      environments), not read back from ``accepted_env_var_names`` — so an
      edit that changes what ``get_env`` does fails here whether or not the
      derivation was updated.

    The probe space is bounded to the documented naming system: zero, one
    or two ``LEVH_``/``STACKMEMORY_`` prefixes over each base. A spelling
    outside that system would have to be added to both ``get_env`` and the
    derivation to take effect, and the PR that does so owns updating the
    probe space too.
    """
    from server.core import env as env_module
    from server.core import runtime_config
    from tests.conftest import _STEERING_ENV

    _PREFIXES = ("", "LEVH_", "STACKMEMORY_")

    def observed_read_set(base: str) -> set[str]:
        """Names that actually change ``get_env(base, ...)'s result.

        The stem is the base name with any known prefix stripped, so the
        space covers every prefixed spelling of it (``STACKMEMORY_CONFIG_PATH``
        for the base ``LEVH_CONFIG_PATH`` included) up to two prefixes.
        """
        stem = base
        for prefix in ("LEVH_", "STACKMEMORY_"):
            stem = stem.removeprefix(prefix)
        baseline = env_module.get_env(base, None, environ={})
        space = {p1 + p2 + stem for p1 in _PREFIXES for p2 in _PREFIXES}
        return {
            candidate
            for candidate in space
            if env_module.get_env(base, None, environ={candidate: "X"}) != baseline
        }

    bases = {
        key
        for key, setting in runtime_config._ENV_TO_KEY.items()
        if "path" in setting
    } | {runtime_config.CONFIG_PATH_ENV, PINNED_NAME}

    for base in sorted(bases):
        claimed = set(env_module.accepted_env_var_names(base))
        observed = observed_read_set(base)

        unscrubbed = observed - set(_STEERING_ENV)
        assert not unscrubbed, (
            f"get_env({base!r}) reads {sorted(unscrubbed)} but tests/conftest.py "
            "does not scrub them; a machine setting one bypasses the suite's "
            "store pin and reads or writes the developer's real memory"
        )
        overclaimed = claimed - observed
        assert not overclaimed, (
            f"accepted_env_var_names({base!r}) claims {sorted(overclaimed)} but "
            "get_env does not read them; the derivation has drifted from the "
            "behavior conftest.py pins against"
        )


def test_the_suite_scrubs_the_behaviour_flags():
    """A flag a developer exports must not reach a test.

    ``LEVH_RECALL_LOG`` is not a path, so the store pin does not touch it, yet
    it decides whether recalls are recorded and reddens the recall-log tests
    when a developer exports it. ``LEVH_PUBLIC_DEMO`` is the same trap one
    layer up: exported to serve a read-only demo, it turns every writing route
    test into a 403. The autouse scrub is the fix; this asserts each flag is
    actually absent from the suite's environment, so an edit that drops one
    from the tuple fails here.
    """
    from tests.conftest import _FLAG_ENV

    for name in _FLAG_ENV:
        assert name not in os.environ, (
            f"{name} steers test behaviour; tests/conftest.py must scrub it or "
            "a developer who exports it reddens the suite locally while CI "
            "stays green"
        )


def test_the_suite_scrubs_every_accepted_spelling_of_every_flag():
    """A flag read through ``get_env`` has more than one spelling.

    The 2026-10-27 report, six flags wide: a developer exports ``LEVH_LOG_JSON``
    (structured logs), ``LEVH_DOGFOOD_ENABLED`` (local instrumentation),
    ``LEVH_DOCTOR_NO_LIVE_PROBE`` (skip the live probe),
    ``LEVH_ONBOARDING_RECEIPT_PATH`` (keep the receipt out of ``$HOME``) or
    ``LEVH_SUPERSESSION`` (prototype retirement), and the suite reddens locally
    while CI — carrying none of them — stays green. ``get_env`` also accepts
    the bare and legacy ``STACKMEMORY_*`` spellings, so scrubbing only the
    ``LEVH_`` name would leave two live bypasses. Each base must be scrubbed
    through every accepted spelling, derived from the same source of truth.
    """
    from server.core.env import accepted_env_var_names
    from tests.conftest import _FLAG_ENV, _FLAG_BASE_NAMES

    for base in _FLAG_BASE_NAMES:
        for spelling in accepted_env_var_names(base):
            assert spelling in _FLAG_ENV, (
                f"{spelling} is an accepted spelling of {base} that get_env "
                "reads, but tests/conftest.py does not scrub it; a developer "
                "exporting it steers the suite"
            )


def test_a_subprocess_with_a_supersession_flag_stays_off():
    """End to end: the prototype flag must not reach the child.

    Reproduces the 2026-10-27 report with the flag whose blast radius is
    largest: ``LEVH_SUPERSESSION=1`` retires every near-identical predecessor
    on write, which flips 25 decision/consolidation/context assertions. The
    child inherits the flag; conftest's scrub is the only thing that can make
    it pass.
    """
    env = dict(os.environ)
    env["LEVH_SUPERSESSION"] = "1"
    env["EMBEDDER_MODE"] = "hash"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_decisions.py::test_decisions_most_recent_first",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_subprocess_with_json_logging_on_still_reads_record_text():
    """End to end: ``LEVH_LOG_JSON`` must not leak into a child either.

    The same report, over the flag that reconfigures the root logger rather
    than a value: with JSON on, ``install_logging`` renders records with
    ``JsonFormatter``, and a test that reads ``record.message`` must still see
    the text. conftest scrubs the flag and the formatter honours the stdlib
    contract; this holds both in place from a child process.
    """
    env = dict(os.environ)
    env["LEVH_LOG_JSON"] = "1"
    env["EMBEDDER_MODE"] = "hash"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_derived_rebuild_resilience.py::test_failure_logged_with_traceback",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_json_formatter_keeps_the_record_message_contract():
    """``JsonFormatter`` must set ``record.message`` like the base formatter.

    ``logging.Formatter.format`` sets ``record.message`` as a documented side
    effect; pytest's ``caplog`` and any second handler read it back. Overriding
    ``format`` without preserving that contract made ``record.message`` raise
    ``AttributeError`` whenever JSON mode was on — the failure surfaced only
    when an earlier test had turned ``LEVH_LOG_JSON`` on, so the suite was
    green or red depending on test order.
    """
    import logging

    from server.core.logging import JsonFormatter

    record = logging.LogRecord(
        name="levh.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="derived-state rebuild failed",
        args=(),
        exc_info=None,
    )
    rendered = JsonFormatter().format(record)

    assert record.message == "derived-state rebuild failed", (
        "JsonFormatter must set record.message; caplog and other handlers read "
        "it back and otherwise raise AttributeError"
    )
    assert json.loads(rendered)["message"] == "derived-state rebuild failed"


def test_install_logging_does_not_leak_into_the_next_test(
    monkeypatch: pytest.MonkeyPatch,
):
    """A test that turns JSON logging on must not reconfigure later tests.

    ``install_logging`` rewrites the root logger's handlers and formatters,
    which monkeypatch does not undo. Without the conftest snapshot/restore, the
    formatter stays installed for the rest of the session, and a later test
    that asserts on captured record text fails for a reason unrelated to its
    own change. This caches the hostile state and the next test asserts the
    suite restored it — the same first-half/second-half shape the engine cache
    tests use.
    """
    import logging

    from server.core import logging as levh_logging

    monkeypatch.setenv(levh_logging.LOG_JSON_ENV, "1")
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    levh_logging.install_logging()
    assert isinstance(handler.formatter, levh_logging.JsonFormatter)


def test_the_next_test_sees_the_original_logging_state():
    """Second half: the root logger must be back to what conftest saved."""
    import logging

    from server.core.logging import JsonFormatter

    root = logging.getLogger()
    leaked = [
        handler
        for handler in root.handlers
        if isinstance(handler.formatter, JsonFormatter)
    ]
    assert not leaked, (
        "install_logging's formatter survived a test boundary; the suite's "
        "logging state is not its own"
    )


def test_the_suite_scrubs_every_embedder_mode_spelling():
    """A machine-level embedder mode must not outrank the config a test writes.

    The 2026-10-27 report: ``LEVH_EMBEDDER_MODE=local`` reddened seven config
    tests that write ``embedder_mode="hash"`` into a temp config and delete
    only the bare ``EMBEDDER_MODE``. ``get_env`` prefers the prefixed spelling,
    so the deletion missed. No path is involved, so the store pin does not
    cover it; CI carries no such variable, so the suite stayed green exactly
    where it is trusted most.
    """
    from server.core.env import accepted_env_var_names
    from tests.conftest import _CONFIG_ENV

    spellings = accepted_env_var_names("EMBEDDER_MODE")
    assert set(spellings) <= set(_CONFIG_ENV), (
        f"tests/conftest.py scrubs {sorted(_CONFIG_ENV)} but get_env reads "
        f"{sorted(spellings)}; a machine setting the missing spelling outranks "
        "the embedder mode a config test writes"
    )
    # The prefixed and legacy spellings must be gone; the bare name carries
    # the suite's own baseline, which a machine export must not change.
    for name in spellings:
        if name != "EMBEDDER_MODE":
            assert name not in os.environ, (
                f"{name} outranks the bare EMBEDDER_MODE a test sets; "
                "tests/conftest.py must scrub every accepted spelling"
            )
    assert os.environ.get("EMBEDDER_MODE") == "hash", (
        "tests/conftest.py must pin the suite's own embedder baseline (hash) "
        "instead of inheriting the machine's value"
    )


def test_a_subprocess_with_a_prefixed_embedder_mode_keeps_the_config():
    """End to end: the child inherits the hostile spelling and must ignore it.

    Reproduces the 2026-10-27 report with the exact pair that failed: the test
    writes ``embedder_mode="hash"`` into a temp config and deletes only the
    bare name. conftest's scrub is the only thing that can make it pass.
    """
    env = dict(os.environ)
    env.pop("EMBEDDER_MODE", None)
    env["LEVH_EMBEDDER_MODE"] = "local"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_runtime_config.py::test_memory_engine_defaults_use_canonical_config",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_suite_pins_the_doctor_probe_environment():
    """Doctor's two out-of-store probes must not reach the developer's machine.

    ``levh doctor`` inspects the interpreter's own site-packages for ``~``
    debris and the supervisor's update state under the real user profile.
    Neither is the store the suite pins, so a developer with a wedged pip or a
    checkout the supervisor stopped updating reddens every test asserting a
    clean verdict — the 2026-10-27 report. conftest points both at empty temp
    paths; this holds that in place, and keeps the pinned site-packages
    unreachable from inside the workspace.
    """
    from server.core import pip_debris

    override = os.environ.get(pip_debris.SITE_PACKAGES_OVERRIDE_ENV, "")
    assert override, (
        "tests/conftest.py must set "
        f"{pip_debris.SITE_PACKAGES_OVERRIDE_ENV}: doctor inspects the real "
        "site-packages otherwise, and a developer's `~` debris reddens the suite"
    )
    assert REPO_ROOT not in Path(override).resolve().parents, (
        f"the doctor debris probe points at {override}, inside the workspace; "
        "the developer's real site-packages must be unreachable from a test"
    )

    log_dir = os.environ.get("LEVH_SUPERVISOR_LOG_DIR", "")
    assert log_dir, (
        "tests/conftest.py must set LEVH_SUPERVISOR_LOG_DIR: doctor reads the "
        "supervisor state under the real user profile otherwise, and a stuck "
        "auto-update reddens every clean-verdict test"
    )
    assert REPO_ROOT not in Path(log_dir).resolve().parents, (
        f"the auto-update state probe points at {log_dir}, inside the "
        "workspace; the developer's real state must be unreachable from a test"
    )


def test_a_subprocess_with_the_flag_exported_still_sees_the_default():
    """A machine exporting ``LEVH_RECALL_LOG`` must not redden the suite.

    Reproduces the 2026-10-01 report, from the other side of the default:
    logging is now on unless the flag says otherwise, so the hostile value is
    ``0`` and the test it would flip is the one asserting a recall leaves a
    row. The child inherits the planted flag; conftest's scrub is the only
    thing that can make it pass.
    """
    env = dict(os.environ)
    env["LEVH_RECALL_LOG"] = "0"
    env["EMBEDDER_MODE"] = "hash"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_recall_log.py::test_a_recall_records_a_row_by_default",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
        [sys.executable, "-m", "server.cli", "setup", "--client", "claude", "--profile", "work"],
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
