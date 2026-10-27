"""pip rename debris: found, moved aside reversibly, reported (issue #521).

An interrupted install leaves `~`-prefixed remnants in site-packages; pip
reads them as invalid distributions and wedges the interpreter's pip until
they are gone — invisible until somebody runs pip by hand. These tests pin
the helper's contract (find, move aside with a reason per failure, a
recovery command that names every path) and the `levh doctor` surface that
reports the wedge with its way out.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sysconfig
from pathlib import Path

from server.core import pip_debris


def _site_with_debris(tmp_path):
    root = tmp_path / "site-packages"
    root.mkdir()
    for name in ("~erver", "~evh-2.32.0.dist-info", "~umpy.libs", "numpy", "levh-2.35.0.dist-info"):
        (root / name).mkdir()
    return root


def test_find_names_only_the_rename_remnants(tmp_path):
    root = _site_with_debris(tmp_path)

    found = pip_debris.find_rename_debris(root)

    assert [p.name for p in found] == ["~erver", "~evh-2.32.0.dist-info", "~umpy.libs"]


def test_find_of_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert pip_debris.find_rename_debris(tmp_path / "not-here") == []


def test_move_aside_clears_the_tree_without_destroying_anything(tmp_path):
    root = _site_with_debris(tmp_path)

    moved, failed = pip_debris.move_aside(pip_debris.find_rename_debris(root), root)

    assert failed == []
    assert pip_debris.find_rename_debris(root) == []
    # Real distributions are untouched.
    assert (root / "numpy").is_dir()
    assert (root / "levh-2.35.0.dist-info").is_dir()
    # Every remnant survives in a backup beside site-packages: nothing was
    # deleted, so a recovery that turns out to be wrong stays reversible.
    backups = [p for p in tmp_path.iterdir() if p.name.startswith(pip_debris.BACKUP_DIR_PREFIX)]
    assert len(backups) == 1
    assert sorted(p.name for p in backups[0].iterdir()) == sorted(p.name for p in moved)


def test_move_aside_reports_what_it_could_not_move(monkeypatch, tmp_path):
    root = _site_with_debris(tmp_path)
    real_move = pip_debris.shutil.move

    def _blocked(src, dst, *args, **kwargs):
        if str(src).endswith("~umpy.libs"):
            raise PermissionError("~umpy.libs is held by another process")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(pip_debris.shutil, "move", _blocked)

    moved, failed = pip_debris.move_aside(pip_debris.find_rename_debris(root), root)

    assert [p.name for p in moved] == ["~erver", "~evh-2.32.0.dist-info"]
    assert [(p.name, error) for p, error in failed] == [
        ("~umpy.libs", "~umpy.libs is held by another process")
    ]
    # The blocked remnant is exactly where the warning will keep naming it.
    assert (root / "~umpy.libs").is_dir()


def test_move_aside_of_nothing_touches_nothing(tmp_path):
    root = tmp_path / "site-packages"
    root.mkdir()

    moved, failed = pip_debris.move_aside([], root)

    assert (moved, failed) == ([], [])
    assert [p.name for p in tmp_path.iterdir()] == ["site-packages"]


def test_recovery_command_names_every_path_on_this_platform(tmp_path):
    root = _site_with_debris(tmp_path)
    blocked = pip_debris.find_rename_debris(root)

    command = pip_debris.recovery_command(blocked, root)

    assert str(root) in command
    for path in blocked:
        assert path.name in command
    assert f"../{pip_debris.BACKUP_DIR_PREFIX}" in command


def test_recovery_command_speaks_powershell_on_windows(tmp_path, monkeypatch):
    """The operator runs the command in *their* shell, so it must be theirs."""
    monkeypatch.setattr(pip_debris.os, "name", "nt")
    root = _site_with_debris(tmp_path)

    command = pip_debris.recovery_command(pip_debris.find_rename_debris(root), root)

    assert command.startswith("Set-Location -LiteralPath")
    assert "Move-Item -LiteralPath" in command
    assert "~evh-2.32.0.dist-info" in command


def test_advice_names_the_paths_the_reason_and_the_command(tmp_path):
    root = _site_with_debris(tmp_path)
    failed = [(p, "file in use") for p in pip_debris.find_rename_debris(root)]

    advice = pip_debris.debris_advice(failed, root)

    assert "could not be moved aside" in advice
    for path, _ in failed:
        assert str(path) in advice
    assert "file in use" in advice
    assert "invalid distributions" in advice
    assert "do not delete" in advice
    assert pip_debris.recovery_command([p for p, _ in failed], root) in advice


# -- levh doctor ------------------------------------------------------------


def _doctor_output(tmp_path, monkeypatch):
    """Drive ``cmd_doctor`` with debris in the interpreter it inspects."""
    root = _site_with_debris(tmp_path)
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: root)
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "store.db"))
    monkeypatch.setenv("EMBEDDER_MODE", "hash")
    monkeypatch.setenv("LEVH_DOCTOR_NO_LIVE_PROBE", "1")

    from server.cli import cmd_doctor

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cmd_doctor(argparse.Namespace())
    return code, buf.getvalue()


def test_doctor_fails_on_rename_debris_and_prints_the_recovery(tmp_path, monkeypatch):
    code, out = _doctor_output(tmp_path, monkeypatch)

    assert "Pip debris" in out
    assert "FAIL" in out
    # The report names every remnant and the command that moves them aside.
    assert "~evh-2.32.0.dist-info" in out
    assert "~umpy.libs" in out
    assert pip_debris.BACKUP_DIR_PREFIX in out
    assert code == 1  # a wedged pip must not pass silently


def test_target_site_packages_honours_the_isolation_override(tmp_path, monkeypatch):
    """The suite's pin, not the interpreter's real purelib, decides the probe.

    ``levh doctor`` inspects the interpreter it runs under; on a developer
    machine with interrupted-install ``~`` debris that makes every clean-verdict
    test fail. The override is what conftest sets to break the dependency — and
    unset, the real ``purelib`` must still be the answer, or production would
    inspect the wrong directory.
    """
    monkeypatch.delenv(pip_debris.SITE_PACKAGES_OVERRIDE_ENV, raising=False)
    assert pip_debris.target_site_packages() == Path(sysconfig.get_paths()["purelib"])

    pinned = tmp_path / "pinned-site-packages"
    monkeypatch.setenv(pip_debris.SITE_PACKAGES_OVERRIDE_ENV, str(pinned))
    assert pip_debris.target_site_packages() == pinned
