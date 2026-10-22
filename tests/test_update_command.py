"""``levh update``: fast-forward a git checkout, refuse anything riskier.

Every test drives real git against a throwaway bare remote, because the
command's whole value is in how it reacts to real repository states. The
host's git config (signing, default branch, hooks) is shut out per test.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from server.commands import update as update_mod
from server.commands.update import (
    CommandError,
    UpdateResult,
    cmd_update,
    run_bounded,
    update_checkout,
)
from server.core import pip_debris


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.mktemp("gitcfg") / "config"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "t")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "t@example.com")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"change {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """(upstream working copy that pushes, local checkout under test)."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    upstream = tmp_path / "upstream"
    _git(tmp_path, "clone", "-q", str(remote), str(upstream))
    _git(upstream, "checkout", "-q", "-b", "main")
    _commit(upstream, "app.py", "v1\n")
    _git(upstream, "push", "-q", "origin", "main")
    local = tmp_path / "local"
    _git(tmp_path, "clone", "-q", str(remote), str(local))
    return upstream, local


def _push_new_commit(upstream: Path, name: str = "app.py", text: str = "v2\n") -> str:
    head = _commit(upstream, name, text)
    _git(upstream, "push", "-q", "origin", "main")
    return head


def _ok(repo: Path) -> tuple[bool, str]:
    return True, ""


# -- repository states --------------------------------------------------------


def test_up_to_date_checkout_is_left_alone(repos):
    _, local = repos
    before = _git(local, "rev-parse", "HEAD")
    result = update_checkout(local)
    assert result.status == "up_to_date"
    assert result.old_head == result.new_head == before


def test_behind_checkout_is_fast_forwarded(repos):
    upstream, local = repos
    new_head = _push_new_commit(upstream)
    result = update_checkout(local, sync_deps=_ok)
    assert result.status == "updated"
    assert result.new_head == new_head
    assert result.deps_synced is None
    assert _git(local, "rev-parse", "HEAD") == new_head
    assert (local / "app.py").read_text(encoding="utf-8") == "v2\n"


def test_check_only_reports_without_moving_head(repos):
    upstream, local = repos
    before = _git(local, "rev-parse", "HEAD")
    _push_new_commit(upstream)
    result = update_checkout(local, check_only=True)
    assert result.status == "behind"
    assert result.behind == 1
    assert _git(local, "rev-parse", "HEAD") == before


def test_local_edits_block_the_update(repos):
    upstream, local = repos
    _push_new_commit(upstream)
    (local / "app.py").write_text("hand edit\n", encoding="utf-8")
    result = update_checkout(local)
    assert result.status == "skipped"
    assert "local changes" in result.reason
    assert (local / "app.py").read_text(encoding="utf-8") == "hand edit\n"


def test_untracked_files_do_not_block_the_update(repos):
    # Runtime artifacts (logs, *.db) live untracked in a checkout.
    upstream, local = repos
    (local / "server.log").write_text("noise\n", encoding="utf-8")
    _push_new_commit(upstream)
    assert update_checkout(local, sync_deps=_ok).status == "updated"
    assert (local / "server.log").exists()


def test_other_branch_is_never_moved(repos):
    upstream, local = repos
    _git(local, "checkout", "-q", "-b", "experiment")
    before = _git(local, "rev-parse", "HEAD")
    _push_new_commit(upstream)
    result = update_checkout(local)
    assert result.status == "skipped"
    assert "experiment" in result.reason
    assert _git(local, "rev-parse", "HEAD") == before


def test_diverged_history_is_not_merged(repos):
    upstream, local = repos
    _push_new_commit(upstream)
    local_head = _commit(local, "mine.py", "local only\n")
    result = update_checkout(local)
    assert result.status == "skipped"
    assert "diverged" in result.reason
    assert _git(local, "rev-parse", "HEAD") == local_head


def test_local_commits_ahead_of_upstream_are_named_as_such(repos):
    _, local = repos
    _commit(local, "mine.py", "local only\n")
    result = update_checkout(local)
    assert result.status == "skipped"
    assert "local commits not on" in result.reason


def test_not_a_checkout_is_skipped(tmp_path):
    # tmp_path may sit inside an unrelated repository (a versioned home
    # directory): that must still read as "not a checkout", never be updated.
    result = update_checkout(tmp_path)
    assert result.status == "skipped"
    assert "not a git checkout" in result.reason


def test_unreachable_remote_is_an_error(repos):
    _, local = repos
    _git(local, "remote", "set-url", "origin", str(local.parent / "missing.git"))
    result = update_checkout(local)
    assert result.status == "error"
    assert "fetch" in result.reason


def test_missing_git_is_an_error_not_a_skip(repos, monkeypatch):
    _, local = repos

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(update_mod, "run_bounded", no_git)
    result = update_checkout(local)
    assert result.status == "error"
    assert "git is not installed" in result.reason


# -- dependencies -------------------------------------------------------------


def test_dependency_sync_runs_only_when_packaging_changed(repos):
    upstream, local = repos
    synced: list[Path] = []

    def record(repo: Path) -> tuple[bool, str]:
        synced.append(repo)
        return True, ""

    _push_new_commit(upstream)
    assert update_checkout(local, sync_deps=record).deps_synced is None
    assert synced == []

    _push_new_commit(upstream, "pyproject.toml", "[project]\nname = 'x'\n")
    result = update_checkout(local, sync_deps=record)
    assert result.deps_synced is True
    assert synced == [local]


def test_failed_dependency_sync_rolls_the_checkout_back(repos):
    upstream, local = repos
    before = _git(local, "rev-parse", "HEAD")
    _push_new_commit(upstream, "uv.lock", "lock v2\n")
    result = update_checkout(local, sync_deps=lambda repo: (False, "no network"))
    assert result.status == "error"
    assert result.deps_synced is False
    assert "no network" in result.reason
    assert "rolled back" in result.reason
    assert _git(local, "rev-parse", "HEAD") == before
    assert not (local / "uv.lock").exists()


def test_installer_prefers_pip_then_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod.importlib.util, "find_spec", lambda name: object())
    assert update_mod._installer_command(tmp_path)[1:3] == ["-m", "pip"]

    monkeypatch.setattr(update_mod.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(update_mod.shutil, "which", lambda name: "/bin/uv")
    assert update_mod._installer_command(tmp_path)[:3] == ["/bin/uv", "pip", "install"]

    monkeypatch.setattr(update_mod.shutil, "which", lambda name: None)
    assert update_mod._installer_command(tmp_path) is None
    ok, detail = update_mod.install_editable(tmp_path)
    assert ok is False
    assert "neither pip nor uv" in detail


def test_install_failure_detail_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "_installer_command", lambda repo: [sys.executable, "-c",
                        "import sys; sys.stderr.write('resolver exploded\\n'); sys.exit(1)"])
    ok, detail = update_mod.install_editable(tmp_path)
    assert ok is False
    assert "resolver exploded" in detail


# -- pip rename debris (issue #521) -----------------------------------------


def _debris_site(tmp_path):
    root = tmp_path / "site-packages"
    root.mkdir()
    (root / "~evh-2.32.0.dist-info").mkdir()
    (root / "~umpy").mkdir()
    return root


def test_install_moves_debris_aside_before_pip_runs(monkeypatch, tmp_path):
    """The wedge is gone before pip starts — not rediscovered by pip."""
    root = _debris_site(tmp_path)
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: root)
    # The stand-in installer fails if a `~` remnant is still visible when it
    # runs, so this pins the ORDER of the cleanup, not just the end state.
    probe = (
        "import os, sys; "
        f"bad = [n for n in os.listdir({str(root)!r}) if n.startswith('~')]; "
        "sys.exit(1 if bad else 0)"
    )
    monkeypatch.setattr(update_mod, "_installer_command", lambda repo: [sys.executable, "-c", probe])

    ok, detail = update_mod.install_editable(tmp_path)

    assert ok is True, detail
    assert pip_debris.find_rename_debris(root) == []
    backups = [p for p in tmp_path.iterdir() if p.name.startswith(pip_debris.BACKUP_DIR_PREFIX)]
    assert backups
    assert {p.name for p in backups[0].iterdir()} == {"~evh-2.32.0.dist-info", "~umpy"}


def test_install_warns_loudly_when_debris_cannot_be_moved(monkeypatch, tmp_path, capsys):
    """Unmovable debris must be named loudly, never left silently wedged."""
    root = _debris_site(tmp_path)
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: root)

    def _blocked(src, dst, *args, **kwargs):
        raise PermissionError("file in use by another process")

    monkeypatch.setattr(pip_debris.shutil, "move", _blocked)
    monkeypatch.setattr(update_mod, "_installer_command", lambda repo: [sys.executable, "-c", "pass"])

    ok, detail = update_mod.install_editable(tmp_path)

    # The warning does not fail an install that can still be attempted.
    assert ok is True, detail
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "~evh-2.32.0.dist-info" in err and "~umpy" in err  # offending directories, by name
    assert "file in use by another process" in err  # why each one stayed
    assert pip_debris.BACKUP_DIR_PREFIX in err  # the recovery command
    # Nothing was destroyed: the remnants are still there to recover.
    remaining = [p.name for p in pip_debris.find_rename_debris(root)]
    assert remaining == ["~evh-2.32.0.dist-info", "~umpy"]


def test_failed_install_names_debris_pip_left_behind(monkeypatch, tmp_path):
    """A run that breaks partway must not walk away from its own casualties."""
    root = tmp_path / "site-packages"
    root.mkdir()
    monkeypatch.setattr(pip_debris, "target_site_packages", lambda: root)
    litter = f"import os; os.mkdir(os.path.join({str(root)!r}, '~exploded'))"
    monkeypatch.setattr(
        update_mod,
        "_installer_command",
        lambda repo: [sys.executable, "-c", f"{litter}; import sys; sys.stderr.write('boom\\n'); sys.exit(1)"],
    )

    ok, detail = update_mod.install_editable(tmp_path)

    assert ok is False
    assert "install failed: boom" in detail
    assert "pip left rename debris behind (~exploded)" in detail


# -- bounded execution --------------------------------------------------------


def test_run_bounded_kills_a_hung_command(tmp_path):
    started = time.monotonic()
    with pytest.raises(CommandError, match="timed out"):
        run_bounded([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, timeout=1)
    assert time.monotonic() - started < 30


def test_run_bounded_gives_the_command_no_stdin(tmp_path):
    out = run_bounded([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
                      cwd=tmp_path, timeout=30)
    assert out.stdout.strip() == "''"


def test_git_runs_without_prompts(monkeypatch, tmp_path):
    seen = {}

    def capture(cmd, *, cwd, timeout, env=None):
        seen.update(env or {})
        return update_mod._Output(0, "", "")

    monkeypatch.setattr(update_mod, "run_bounded", capture)
    update_mod.git(tmp_path, "status")
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["GCM_INTERACTIVE"] == "never"


# -- CLI ----------------------------------------------------------------------


def test_cmd_update_json_output(repos, monkeypatch, capsys):
    upstream, local = repos
    new_head = _push_new_commit(upstream)
    monkeypatch.setattr(update_mod, "repo_root", lambda: local)
    args = argparse.Namespace(check=False, remote="origin", branch="main", json=True)
    assert cmd_update(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "updated"
    assert payload["new_head"] == new_head


def test_cmd_update_exit_code_for_error(monkeypatch, capsys):
    monkeypatch.setattr(
        update_mod,
        "update_checkout",
        lambda *a, **kw: UpdateResult(status="error", old_head="", new_head="", reason="boom"),
    )
    args = argparse.Namespace(check=False, remote="origin", branch="main", json=False)
    assert cmd_update(args) == 1
    assert "boom" in capsys.readouterr().out
