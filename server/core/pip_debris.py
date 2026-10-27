"""pip rename debris: the `~` remnants an interrupted install leaves behind.

A pip install or uninstall renames the distribution it is about to replace to
a `~`-prefixed name, moves the new files in, then deletes the renamed copy.
When that sequence is interrupted — a killed updater, a reboot, a locked file —
the `~` copy stays behind. pip reads it as an invalid distribution and the
interpreter's whole site-packages wedges: install, uninstall and show all fail
until the remnant is moved out (issue #521).

Nothing here deletes. Remnants are moved into a backup directory *beside*
site-packages, because a remnant may be the only remaining copy of what it
holds — the recovery must stay reversible.
"""

from __future__ import annotations

import os
import shutil
import sysconfig
import time
from pathlib import Path
from typing import Sequence

from server.core.env import get_env

#: pip's own marker for "I was renaming this when I stopped". A real
#: distribution name starts with an alphanumeric (PEP 508), so a top-level
#: entry beginning with `~` can only be an unfinished rename — including
#: ``<name>.dist-info`` variants, which are what pip refuses to proceed over.
DEBRIS_PREFIX = "~"

#: Sibling of site-packages, never inside it: pip must not scan it again, and
#: the backup must survive independently of the directory it was rescued from.
BACKUP_DIR_PREFIX = "levh-pip-debris-backup"

#: Test-isolation override for the inspected directory. Both ``levh doctor``
#: and ``levh update`` report on the interpreter they run under, so a
#: developer whose own site-packages carry ``~`` debris reddens every test that
#: asserts a clean verdict — the machine-specific failure
#: ``LEVH_DOCTOR_NO_LIVE_PROBE`` exists to prevent for the live probe. The
#: suite points this at an empty directory; unset in production, where the
#: interpreter's real ``purelib`` is the only correct answer.
SITE_PACKAGES_OVERRIDE_ENV = "LEVH_SITE_PACKAGES_OVERRIDE"


def target_site_packages() -> Path:
    """The directory this interpreter's pip writes to (its ``purelib``)."""
    override = get_env(SITE_PACKAGES_OVERRIDE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(sysconfig.get_paths()["purelib"])


def find_rename_debris(root: Path) -> list[Path]:
    """The `~`-prefixed remnants at the top of *root*, in a stable order.

    An unreadable directory yields nothing: reporting debris is best-effort —
    a permission problem there is its own problem for the caller to hit later.
    """
    try:
        return sorted(p for p in root.iterdir() if p.name.startswith(DEBRIS_PREFIX) and p.is_dir())
    except OSError:
        return []


def move_aside(paths: Sequence[Path], root: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Move *paths* (remnants of *root*) into a fresh backup directory.

    Returns ``(moved, [(path, error), ...])``: whatever could not be moved
    stays exactly where it was, named with the reason, so the caller can warn
    instead of pretending the tree is clean.
    """
    moved: list[Path] = []
    failed: list[tuple[Path, str]] = []
    if not paths:
        return moved, failed
    backup = root.parent / f"{BACKUP_DIR_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
    for path in paths:
        try:
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(backup / path.name))
        except OSError as exc:
            failed.append((path, str(exc)))
        else:
            moved.append(path)
    return moved, failed


def _sh_quote(value: str) -> str:
    """Single-quote *value* for a POSIX shell."""
    return "'" + value.replace("'", "'\\''") + "'"


def _ps_quote(value: str) -> str:
    """Single-quote *value* for PowerShell."""
    return "'" + value.replace("'", "''") + "'"


def recovery_command(paths: Sequence[Path], root: Path) -> str:
    """One copy-pasteable command that moves *paths* out of *root*'s way.

    Built from basenames after moving into *root*, so the line stays readable
    in a doctor report however long the interpreter's path is.
    """
    if os.name == "nt":
        listed = ", ".join(f"'{_ps_quote(path.name)}'" for path in paths)
        return (
            f"Set-Location -LiteralPath '{_ps_quote(str(root))}'; "
            f"New-Item -ItemType Directory -Force '../{BACKUP_DIR_PREFIX}' | Out-Null; "
            f"Move-Item -LiteralPath {listed} -Destination '../{BACKUP_DIR_PREFIX}'"
        )
    listed = " ".join(f"'{_sh_quote(path.name)}'" for path in paths)
    return (
        f"cd {_sh_quote(str(root))} && mkdir -p '../{BACKUP_DIR_PREFIX}' && "
        f"mv {listed} '../{BACKUP_DIR_PREFIX}/'"
    )


def debris_advice(failed: Sequence[tuple[Path, str]], root: Path) -> str:
    """The loud warning for remnants that stayed behind: what, why, and out."""
    paths = [path for path, _ in failed]
    listed = "\n".join(f"    {path}: {error}" for path, error in failed)
    return (
        f"pip rename debris in {root} could not be moved aside:\n"
        f"{listed}\n"
        "  pip treats these as invalid distributions: install, uninstall and show\n"
        "  fail in this interpreter until they are out of site-packages. Move them\n"
        "  aside yourself (do not delete - a remnant may be the only copy of what\n"
        "  it holds), then rerun the install:\n"
        f"    {recovery_command(paths, root)}"
    )
