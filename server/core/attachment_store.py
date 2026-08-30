"""Where LEVH keeps the attachment files it owns.

Two kinds of file end up in the ``attachments`` table and they are not the
same thing:

* **managed** — uploaded through ``/api/attachments/upload``. LEVH wrote the
  file itself, into ``<database dir>/attachments/``, under a name it invented.
  The user has no other copy; if this one goes, the bytes are gone.
* **referenced** — a file that already existed somewhere the user chose,
  attached by path. LEVH only points at it. The user's own copy is the
  original.

The distinction decides what a portable backup owes the user. A managed file's
bytes belong in the backup, because nothing else holds them. A referenced
file's bytes do not: copying somebody's documents into a backup they asked for
of their *memories* would be taking more than was offered.
"""

from __future__ import annotations

import os
from pathlib import Path


def attachments_dir() -> Path:
    """``<database dir>/attachments``, created if absent."""
    from server.core.runtime_config import resolve_runtime_config

    base = Path(resolve_runtime_config().database_path).resolve().parent
    target = base / "attachments"
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_managed(path: str) -> bool:
    """True when ``path`` is a file LEVH itself wrote into its own store.

    Compared by resolved parent directory rather than by prefix: a string
    prefix test would call ``/data/attachments-old/x.png`` managed, and would
    miss the same file reached through a symlink or a different case on
    Windows.
    """
    try:
        parent = Path(path).resolve().parent
    except (OSError, ValueError):
        return False
    try:
        return os.path.samefile(parent, attachments_dir())
    except OSError:
        # samefile needs both to exist; if the parent is gone it is not the
        # live store, whatever it once was.
        return False
