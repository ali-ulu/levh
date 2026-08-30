"""The receipt fallback has to survive a directory that exists but cannot be
written to.

PR #65 added the fallback for a read-only *cwd*: if `.stackmemory` could not be
created, write to `~/.stackmemory` instead. It checked creatability —
``mkdir(parents=True, exist_ok=True)`` — which succeeds as a no-op against a
`.stackmemory` that already exists and is read-only. The check passed, the
local path came back, and the next ``write_text`` raised PermissionError: the
fallback the function exists for never ran.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from server.core import onboarding


RECEIPT_FIELDS = dict(
    database_ready=True,
    first_memory_ready=True,
    mcp_client="claude_code",
    mcp_profile="work",
    demo_mode=False,
    dogfood_enabled=False,
)

# chmod on a directory does not remove write access for the owner on Windows,
# so the read-only cases can only be exercised where POSIX permissions apply.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="directory permissions are not enforced this way on Windows"
)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A cwd of our own, with the receipt env var cleared so the default path
    logic is what actually runs."""
    monkeypatch.delenv(onboarding.RECEIPT_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(onboarding.Path, "home", staticmethod(lambda: home))
    return home


def _read_only(directory):
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)


def test_a_writable_local_directory_is_still_preferred(workdir, fake_home):
    receipt = onboarding.write_receipt(**RECEIPT_FIELDS)

    local = workdir / ".stackmemory" / "onboarding-receipt.json"
    assert local.exists()
    assert json.loads(local.read_text(encoding="utf-8"))["mcp_profile"] == "work"
    assert not (fake_home / ".stackmemory").exists()
    assert receipt["onboarding_version"] == onboarding.ONBOARDING_VERSION


def test_the_probe_leaves_nothing_behind(workdir, fake_home):
    onboarding.write_receipt(**RECEIPT_FIELDS)

    left = [p.name for p in (workdir / ".stackmemory").iterdir()]
    assert left == ["onboarding-receipt.json"]


@posix_only
def test_an_existing_but_unwritable_directory_falls_back(workdir, fake_home):
    # The case PR #65 missed: the directory is already there, so mkdir is a
    # successful no-op and creatability tells you nothing.
    local_dir = workdir / ".stackmemory"
    local_dir.mkdir()
    _read_only(local_dir)
    try:
        assert onboarding._default_receipt_path() == (
            fake_home / ".stackmemory" / "onboarding-receipt.json"
        )

        onboarding.write_receipt(**RECEIPT_FIELDS)

        written = fake_home / ".stackmemory" / "onboarding-receipt.json"
        assert written.exists()
        assert not (local_dir / "onboarding-receipt.json").exists()
    finally:
        local_dir.chmod(stat.S_IRWXU)


@posix_only
def test_a_read_only_cwd_still_falls_back(workdir, fake_home):
    # The case PR #65 did cover — it must keep working.
    _read_only(workdir)
    try:
        onboarding.write_receipt(**RECEIPT_FIELDS)
        assert (fake_home / ".stackmemory" / "onboarding-receipt.json").exists()
    finally:
        workdir.chmod(stat.S_IRWXU)


@posix_only
def test_reads_follow_writes_when_the_local_directory_is_unwritable(workdir, fake_home):
    local_dir = workdir / ".stackmemory"
    local_dir.mkdir()
    _read_only(local_dir)
    try:
        onboarding.write_receipt(**RECEIPT_FIELDS)
        # A receipt written to the fallback and then read from the local path
        # would report "not onboarded" on every subsequent start.
        assert onboarding.read_receipt() is not None
    finally:
        local_dir.chmod(stat.S_IRWXU)


@posix_only
def test_an_explicitly_named_path_fails_loudly(workdir, fake_home):
    # Silently relocating a receipt the caller named would leave them looking
    # for a file that is not where they asked for it.
    named_dir = workdir / "named"
    named_dir.mkdir()
    _read_only(named_dir)
    try:
        with pytest.raises(OSError):
            onboarding.write_receipt(**RECEIPT_FIELDS, path=named_dir / "receipt.json")
        assert not (fake_home / ".stackmemory" / "onboarding-receipt.json").exists()
    finally:
        named_dir.chmod(stat.S_IRWXU)


@posix_only
def test_an_env_named_path_fails_loudly(workdir, fake_home, monkeypatch):
    named_dir = workdir / "env-named"
    named_dir.mkdir()
    _read_only(named_dir)
    monkeypatch.setenv(onboarding.RECEIPT_ENV, str(named_dir / "receipt.json"))
    try:
        with pytest.raises(OSError):
            onboarding.write_receipt(**RECEIPT_FIELDS)
    finally:
        named_dir.chmod(stat.S_IRWXU)


@posix_only
def test_a_failing_fallback_is_not_swallowed(workdir, fake_home):
    # If neither location can be written, the caller must hear about it rather
    # than be told a receipt exists somewhere it does not.
    local_dir = workdir / ".stackmemory"
    local_dir.mkdir()
    _read_only(local_dir)
    home_dir = fake_home / ".stackmemory"
    home_dir.mkdir()
    _read_only(home_dir)
    try:
        with pytest.raises(OSError):
            onboarding.write_receipt(**RECEIPT_FIELDS)
    finally:
        local_dir.chmod(stat.S_IRWXU)
        home_dir.chmod(stat.S_IRWXU)


def test_is_writable_reports_a_missing_directory_as_unwritable(tmp_path):
    assert onboarding._is_writable(tmp_path) is True
    assert onboarding._is_writable(tmp_path / "does-not-exist") is False


# The cases above need POSIX permissions to be real. The two below inject the
# same failures directly, so the wiring is covered on every platform — Windows
# included, where the chmod cases skip.


def test_an_unwritable_probe_result_routes_to_the_fallback(workdir, fake_home, monkeypatch):
    (workdir / ".stackmemory").mkdir()
    monkeypatch.setattr(onboarding, "_is_writable", lambda directory: False)

    assert onboarding._default_receipt_path() == (
        fake_home / ".stackmemory" / "onboarding-receipt.json"
    )

    onboarding.write_receipt(**RECEIPT_FIELDS)
    assert (fake_home / ".stackmemory" / "onboarding-receipt.json").exists()


def test_a_write_that_fails_after_the_probe_still_falls_back(workdir, fake_home, monkeypatch):
    # The probe narrows the window between check and write; it cannot close it.
    # A directory can lose its permissions in between, and no probe predicts a
    # full disk.
    local = workdir / ".stackmemory" / "onboarding-receipt.json"
    original = onboarding.Path.write_text

    def _explode(self, *args, **kwargs):
        # The default receipt path is relative, so it only equals `local` once
        # both are resolved against the cwd.
        if self.resolve() == local.resolve():
            raise PermissionError("permission denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(onboarding.Path, "write_text", _explode)

    onboarding.write_receipt(**RECEIPT_FIELDS)

    assert (fake_home / ".stackmemory" / "onboarding-receipt.json").exists()
    assert not local.exists()
