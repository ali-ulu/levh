"""``levh update``: keep a git checkout on the tip of its upstream branch.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementation.

The command only ever fast-forwards. Anything that would need a decision -
local edits to tracked files, a different branch checked out, a history that
is not a strict ancestor of upstream - is reported and left untouched, so an
unattended caller (``server/supervisor.py``) can run it on a timer without
being able to destroy work. Untracked files (logs, databases) never block it.

When ``pyproject.toml`` or ``uv.lock`` changed, the dependencies are
reinstalled; if that fails the checkout is moved back to where it was
(``git reset --keep``), so the tree on disk never holds code whose
dependencies are missing. Every git and installer call is non-interactive and
time-boxed: a credential prompt or a hung network call becomes an error, not a
frozen process.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from server.core import pip_debris

UpdateStatus = Literal["up_to_date", "updated", "behind", "skipped", "error"]

# A change to either file can change what must be installed for the new code.
PACKAGING_FILES = ("pyproject.toml", "uv.lock")
GIT_TIMEOUT_S = 120
INSTALL_TIMEOUT_S = 900
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Never let git or ssh wait for a human: an unattended run must fail fast.
NON_INTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    # Stable English messages: the "not a git repository" check reads stderr.
    "LC_ALL": "C",
}
# A ref of our own: FETCH_HEAD is shared, so a user's concurrent `git fetch`
# could otherwise swap the commit we are about to fast-forward to.
TARGET_REF = "refs/levh/update-target"


@dataclass(frozen=True)
class UpdateResult:
    status: UpdateStatus
    old_head: str
    new_head: str
    reason: str = ""
    behind: int = 0
    # None: packaging files unchanged, nothing to do. True/False: sync outcome.
    deps_synced: bool | None = None


class CommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Output:
    returncode: int
    stdout: str
    stderr: str


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_bounded(cmd: Sequence[str], *, cwd: Path, timeout: float,
                env: dict[str, str] | None = None) -> _Output:
    """Run ``cmd`` with no stdin and a hard deadline that kills its whole tree.

    ``subprocess.run(timeout=)`` kills only the direct child; on Windows a
    surviving grandchild (git-remote-https, a credential helper) keeps the pipes
    open and ``communicate()`` then blocks forever.
    """
    proc = subprocess.Popen(
        list(cmd), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, start_new_session=sys.platform != "win32",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # a grandchild outside the tree still holds the pipes; give up on them
        raise CommandError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} timed out after {timeout:g}s")
    return _Output(proc.returncode, stdout, stderr)


def _git_env() -> dict[str, str]:
    env = {**os.environ, **NON_INTERACTIVE_ENV}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    return env


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def git(repo: Path, *args: str, check: bool = True) -> _Output:
    try:
        out = run_bounded(["git", "-c", "credential.interactive=never", *args],
                          cwd=repo, timeout=GIT_TIMEOUT_S, env=_git_env())
    except FileNotFoundError as exc:
        raise CommandError("git is not installed or not on PATH") from exc
    if check and out.returncode != 0:
        raise CommandError(f"git {args[0]} failed: {_last_line(out.stderr or out.stdout) or out.returncode}")
    return out


def _installer_command(repo: Path) -> list[str] | None:
    """pip when the interpreter has it, else uv's pip (uv venvs ship no pip)."""
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(repo)]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--quiet", "--python", sys.executable, "-e", str(repo)]
    return None


def _clear_rename_debris() -> None:
    """Move pip's interrupted-rename remnants aside before installing (issue #521).

    A ``~``-prefixed leftover wedges every pip command in this interpreter, so
    an install walking into one could not finish anyway — it is cleaned first,
    not reported after. When the move itself cannot run (no permission, a
    process holding a file), the warning names the offending directories and
    the recovery command instead of leaving the tree silently wedged for the
    next person to discover.
    """
    root = pip_debris.target_site_packages()
    debris = pip_debris.find_rename_debris(root)
    if not debris:
        return
    _moved, failed = pip_debris.move_aside(debris, root)
    if failed:
        print(f"WARNING: {pip_debris.debris_advice(failed, root)}", file=sys.stderr)


def install_editable(repo: Path) -> tuple[bool, str]:
    """Reinstall the checkout into the running interpreter: (ok, detail)."""
    _clear_rename_debris()
    cmd = _installer_command(repo)
    if cmd is None:
        return False, "neither pip nor uv is available to install dependencies"
    try:
        out = run_bounded(cmd, cwd=repo, timeout=INSTALL_TIMEOUT_S)
    except (OSError, CommandError) as exc:
        return False, str(exc)
    if out.returncode != 0:
        detail = f"install failed: {_last_line(out.stderr or out.stdout)}"
        # A run that broke partway can rename its own casualties on the way
        # out; naming them here beats leaving them for the next pip to trip on.
        leftover = pip_debris.find_rename_debris(pip_debris.target_site_packages())
        if leftover:
            names = ", ".join(path.name for path in leftover)
            detail += f"; pip left rename debris behind ({names})"
        return False, detail
    return True, ""


def repo_root() -> Path:
    """The checkout this code runs from (``server/commands/update.py`` -> root)."""
    return Path(__file__).resolve().parents[2]


def _precheck(repo: Path) -> UpdateResult | str:
    """The current HEAD when the checkout may be updated, else the verdict."""
    probe = git(repo, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        # git words the same failure two ways: "not a git repository (or any of
        # the parent directories): .git" when it reaches the filesystem root,
        # and a "…(or any parent up to mount point /)\nStopping at filesystem
        # boundary…" pair when it stops at a mount point first. Which one you
        # get depends on whether the directory shares a filesystem with its
        # parents — /tmp does, a mounted workspace does not — so the check has
        # to read the whole stderr, not just its last line, or a non-checkout on
        # a mount boundary reads as an error.
        if "not a git repository" in probe.stderr:
            return UpdateResult("skipped", "", "", "not a git checkout (a wheel install updates with "
                                "`pip install -U levh`)")
        detail = _last_line(probe.stderr)
        return UpdateResult("error", "", "", f"git cannot read the checkout: {detail}")
    # Rooted at ``repo`` itself: a wheel install that happens to sit inside some
    # unrelated repository (a home directory under version control) must not
    # "update" that repository.
    if Path(probe.stdout.strip()).resolve() != repo.resolve():
        return UpdateResult("skipped", "", "", "not a git checkout root (a wheel install updates with "
                            "`pip install -U levh`)")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return UpdateResult("skipped", head, head, "local changes to tracked files; commit or discard them first")
    return head


def _current_branch(repo: Path) -> str:
    """The branch HEAD points at, or ``""`` when detached."""
    name = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return "" if name == "HEAD" else name


def _branch_refusal(head: str, branch: str, current: str) -> UpdateResult:
    """The verdict for a checkout that is not on the branch auto-update follows.

    Refusing outright is how a checkout got stuck on a feature branch for days
    (issue #522): the supervisor kept skipping and nobody could see it. The
    reason names the branch the user has to move, which is the actionable fact.
    """
    if not current:
        return UpdateResult("skipped", head, head, f"HEAD is detached, auto-update only follows '{branch}'")
    return UpdateResult("skipped", head, head, f"checkout is on '{current}', auto-update only "
                        f"follows '{branch}'")


def _fetch_target(repo: Path, remote: str, branch: str, head: str) -> UpdateResult | str:
    """The fetched upstream commit when HEAD can fast-forward to it, else the verdict."""
    # ``--`` keeps a remote name from being read as an option; the private ref
    # is exact even when the remote's refspec does not track ``branch``.
    git(repo, "fetch", "--quiet", "--no-tags", "--", remote, f"+{branch}:{TARGET_REF}")
    target = git(repo, "rev-parse", TARGET_REF).stdout.strip()
    if target == head:
        return UpdateResult("up_to_date", head, head)
    if git(repo, "merge-base", "--is-ancestor", head, target, check=False).returncode != 0:
        ahead = git(repo, "merge-base", "--is-ancestor", target, head, check=False).returncode == 0
        why = "has local commits not on" if ahead else "diverged from"
        return UpdateResult("skipped", head, head, f"local history {why} {remote}/{branch}; "
                            "not merging unattended")
    return target


def _apply(repo: Path, head: str, target: str,
           sync_deps: Callable[[Path], tuple[bool, str]]) -> UpdateResult:
    behind = int(git(repo, "rev-list", "--count", f"{head}..{target}").stdout.strip())
    changed = git(repo, "diff", "--name-only", head, target).stdout.splitlines()
    git(repo, "merge", "--ff-only", "--quiet", target)
    if not any(name in PACKAGING_FILES for name in changed):
        return UpdateResult("updated", head, target, behind=behind)
    ok, detail = sync_deps(repo)
    if ok:
        return UpdateResult("updated", head, target, behind=behind, deps_synced=True)
    # Never leave code on disk whose dependencies are missing: the next restart
    # (crash, reboot) would boot straight into an ImportError loop.
    rollback = git(repo, "reset", "--keep", head, check=False)
    where = "rolled back" if rollback.returncode == 0 else "ROLLBACK FAILED, checkout left"
    return UpdateResult("error", head, head if rollback.returncode == 0 else target,
                        f"dependency install failed ({detail}); {where} at {head[:8]}",
                        behind=behind, deps_synced=False)


def _branch_own_upstream(repo: Path, current: str) -> str:
    """``current``'s own upstream (``origin/foo``), or ``""`` when it has none."""
    out = git(repo, "rev-parse", "--abbrev-ref", f"{current}@{{upstream}}", check=False)
    return out.stdout.strip() if out.returncode == 0 else ""


def _off_branch_guard(repo: Path, branch: str, current: str, remote: str) -> str | None:
    """``None`` when an off-branch fast-forward is safe, else the extra refusal.

    Two cases must not be moved unattended. A detached HEAD has no branch to
    advance, and a branch that tracks its *own* remote (``origin/foo``) is not
    following the branch auto-update follows, so pulling ``origin/main`` into it
    would be a surprise the user did not ask for.
    """
    if not current:
        return "detached"
    own = _branch_own_upstream(repo, current)
    if own and own not in (f"{remote}/{branch}", branch):
        return f"checkout is on '{current}' which tracks '{own}'; auto-update only follows '{remote}/{branch}'"
    return None


def update_checkout(
    repo: Path,
    *,
    remote: str = "origin",
    branch: str = "main",
    check_only: bool = False,
    sync_deps: Callable[[Path], tuple[bool, str]] = install_editable,
) -> UpdateResult:
    """Fast-forward ``repo`` to ``remote/branch`` when that is provably safe.

    When the checkout sits on another branch, the update still runs if that
    branch has no work of its own: its tip is an ancestor of upstream, so
    advancing it is the same fast-forward, and the branch pointer moves with
    HEAD. Refusing every off-branch checkout is what let one get stuck for days
    (issue #522).
    """
    head = ""
    try:
        checked = _precheck(repo)
        if isinstance(checked, UpdateResult):
            return checked
        head = checked
        current = _current_branch(repo)
        off_branch = current != branch
        fetched = _fetch_target(repo, remote, branch, head)
        if isinstance(fetched, UpdateResult):
            # ``up_to_date`` is unambiguous wherever the checkout sits; a
            # history verdict on another branch is reported as a branch verdict
            # so the reason names the branch the user has to move.
            if off_branch and fetched.status != "up_to_date":
                return _branch_refusal(head, branch, current)
            return fetched
        if off_branch:
            refusal = _off_branch_guard(repo, branch, current, remote)
            if refusal == "detached":
                return _branch_refusal(head, branch, "")
            if refusal is not None:
                return UpdateResult("skipped", head, head, refusal)
        if check_only:
            behind = int(git(repo, "rev-list", "--count", f"{head}..{fetched}").stdout.strip())
            return UpdateResult("behind", head, fetched, behind=behind)
        return _apply(repo, head, fetched, sync_deps)
    except (CommandError, OSError, ValueError) as exc:
        return UpdateResult("error", head, head, str(exc))


def _describe(result: UpdateResult) -> str:
    short_old, short_new = result.old_head[:8], result.new_head[:8]
    if result.status == "updated":
        lines = [f"  Updated {short_old} -> {short_new} ({result.behind} commit(s))."]
        if result.deps_synced:
            lines.append("  Dependencies reinstalled (packaging files changed).")
        lines.append("  Restart `levh serve` to run the new code.")
        return "\n".join(lines)
    if result.status == "behind":
        return f"  {result.behind} commit(s) behind ({short_old} -> {short_new}). Run `levh update`."
    if result.status == "up_to_date":
        return f"  Already up to date ({short_old})."
    label = "Skipped" if result.status == "skipped" else "ERROR"
    return f"  {label}: {result.reason}"


def cmd_update(args: argparse.Namespace) -> int:
    """Fast-forward this checkout; exit 1 when it needs a human's attention."""
    result = update_checkout(
        repo_root(),
        remote=args.remote,
        branch=args.branch,
        check_only=args.check,
    )
    if args.json:
        print(json.dumps(asdict(result)))
    else:
        print(_describe(result))
    return 1 if result.status == "error" else 0
