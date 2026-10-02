"""Git Connector — Import repository history as memories.

Answers *"why does this code look like this?"* from the repository itself
rather than from a chat transcript. Each commit becomes one episodic memory
led by ``<short-sha> <subject>``, with the body, the author and the diffstat in
metadata — so a later recall can say *"this function changed three commits ago,
by Ali, for this reason"*.

Read-only and offline: it shells out to the local ``git`` binary, never to a
network. ``fetch`` only ever runs ``git log``; no worktree, branch or index is
touched, so importing a repository cannot mutate it.

Config keys:
    repo_path (str): Path to the repository **root** (the directory holding
        ``.git``). Required, and deliberately not defaulted: git walks upward
        from any directory, so an implicit "current directory" could import an
        unrelated enclosing repository's history. A subdirectory is refused with
        the correct root named in the error.
    ref (str, optional): Revision to walk. Default ``HEAD``. Accepts a branch,
        tag or sha.
    max_commits (int, optional): Cap on commits read, newest first (default 200).
    since_days (int, optional): Only commits from the last N days.
    since (str, optional): Explicit lower bound (``--since`` value, e.g.
        ``2026-01-01``). Wins over ``since_days`` when both are given.
    author (str, optional): ``--author`` filter.
    include_body (bool, optional): Include the commit body (default True).
    body_chars (int, optional): Cap on the stored body (default 2000).
    timeout_seconds (int, optional): Hard cap on each ``git`` call (default 120)
        so a huge repository cannot hang the caller.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from .base import BaseConnector

# Field / record separators. ASCII unit separator and record separator are
# illegal in a git identity and effectively never appear in a commit message,
# so a single `git log` call yields an unambiguous parse without a second pass.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

# `git log --pretty` placeholders: sha, author name, author email, author date,
# subject, body. The body is last because it may itself contain newlines.
_PRETTY = _FIELD_SEP.join(["%H", "%an", "%ae", "%aI", "%s", "%b"]) + _RECORD_SEP


def parse_git_log(raw: str) -> list[dict]:
    """Parse ``git log`` output produced with :data:`_PRETTY` into dicts.

    Deterministic and total: malformed or partial records are skipped rather
    than raising, so a truncated read degrades to fewer commits, never a crash.
    Returns dicts with keys ``sha``, ``author``, ``email``, ``date``,
    ``subject``, ``body``.
    """
    out: list[dict] = []
    for record in (raw or "").split(_RECORD_SEP):
        if not record.strip():
            continue
        fields = record.split(_FIELD_SEP)
        if len(fields) < 6:
            continue
        sha, author, email, date, subject, body = fields[:6]
        sha = sha.strip()
        if not sha:
            continue
        out.append(
            {
                "sha": sha,
                "author": author.strip(),
                "email": email.strip(),
                "date": date.strip(),
                "subject": subject.strip(),
                "body": body.strip("\n").strip(),
            }
        )
    return out


class GitConnector(BaseConnector):
    """Import local repository history as memories."""

    name: str = "git"
    description: str = (
        "Import commit history from a local git repository (read-only, offline). "
        "Each commit becomes a memory with its author, date and diffstat."
    )

    def __init__(self) -> None:
        self._repo_path: str = ""
        self._ref: str = "HEAD"
        self._max_commits: int = 200
        self._since: str = ""
        self._author: str = ""
        self._include_body: bool = True
        self._body_chars: int = 2000
        self._timeout: int = 120
        self._repo_name: str = ""

    def required_config_keys(self) -> list[str]:
        return ["repo_path"]

    # ── lifecycle ──────────────────────────────────────────────────

    async def connect(self, config: dict) -> bool:
        """Validate the path is a git repository root and normalise the options.

        Raises:
            ValueError: ``repo_path`` is missing, is not a directory, or a
                numeric option is invalid.
            FileNotFoundError: the path, or the ``git`` binary, does not exist.
            ConnectionError: the path is not a repository root.
        """
        repo_path = config.get("repo_path") or ""
        if not repo_path:
            raise ValueError(
                "repo_path is required. Pass the path to a git repository root "
                "(the directory holding .git)."
            )

        path = os.path.realpath(os.path.abspath(os.path.expanduser(str(repo_path))))
        if not os.path.exists(path):
            raise FileNotFoundError(f"Repository path does not exist: {path}")
        if not os.path.isdir(path):
            raise ValueError(f"repo_path is not a directory: {path}")
        if shutil.which("git") is None:
            raise FileNotFoundError(
                "The 'git' executable was not found on PATH; the git connector "
                "shells out to the local git binary."
            )

        # Set before the probes below, which shell out with `git -C`.
        self._repo_path = path
        self._repo_name = os.path.basename(path.rstrip(os.sep)) or path

        self._ref = str(config.get("ref") or "HEAD")
        if not self._ref or self._ref.startswith("-"):
            # A ref beginning with `-` would be parsed by git as an option.
            raise ValueError(
                f"ref must not be empty or start with '-', got {self._ref!r}"
            )
        self._author = str(config.get("author") or "")
        self._include_body = bool(config.get("include_body", True))
        self._body_chars = self._positive_int(config, "body_chars", 2000)
        self._timeout = self._positive_int(config, "timeout_seconds", 120)
        self._max_commits = self._positive_int(config, "max_commits", 200)

        since_days = config.get("since_days")
        if config.get("since"):
            self._since = str(config["since"])
        elif since_days is not None:
            self._since = f"{self._positive_int(config, 'since_days', 0)} days ago"
        else:
            self._since = ""

        # `repo_path` must be the repository *root*, and git's own answer is the
        # authority. This matters more than it looks: git walks upward from any
        # directory, so accepting a non-root path would let a path that merely
        # sits beneath some unrelated repository — a temp directory under a home
        # directory that happens to be a repo — import that outer repository's
        # entire history. "Is this directory inside some working tree" and "did
        # git walk up to a repository I never named" are not reliably separable
        # when one repository is nested inside another, so the connector does not
        # try: it requires the root and fails loudly otherwise.
        top = self._run_git(["rev-parse", "--show-toplevel"])
        if top is None:
            raise ConnectionError(
                f"Not a git repository (or git failed to read it): {path}"
            )
        top = os.path.realpath(os.path.abspath(top.strip().splitlines()[0]))
        if os.path.normcase(top) != os.path.normcase(path):
            raise ConnectionError(
                f"repo_path must be the repository root. {path} resolves to the "
                f"repository at {top}; pass {top} instead."
            )
        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Return one memory-compatible dict per commit, newest first."""
        if not self._repo_path:
            raise RuntimeError("connect() must be called before fetch().")

        args = [
            "log",
            f"--max-count={self._max_commits}",
            f"--pretty=format:{_PRETTY}",
        ]
        if self._since:
            args.append(f"--since={self._since}")
        if self._author:
            args.append(f"--author={self._author}")
        # The ref goes after every option and immediately before the `--`
        # terminator, so it is unambiguously a revision. (A `--` *before* it
        # would make git read it as a pathspec instead.)
        args += [self._ref, "--"]

        raw = self._run_git(args)
        if raw is None:
            return []

        return [self._to_memory(c) for c in parse_git_log(raw)]

    async def disconnect(self) -> None:
        self._repo_path = ""
        self._repo_name = ""

    # ── internal helpers ───────────────────────────────────────────

    @staticmethod
    def _positive_int(config: dict, key: str, default: int) -> int:
        """Coerce a config value to a positive int, or raise ValueError."""
        value = config.get(key, default)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be an integer, got {value!r}") from None
        if number < 0:
            raise ValueError(f"{key} must not be negative, got {number}")
        return number

    def _run_git(self, args: list[str]) -> str | None:
        """Run one git command in the repo. Returns stdout, or None on failure.

        Every failure mode — missing binary, non-zero exit, timeout — collapses
        to ``None`` so callers get "no data" rather than an exception mid-fetch.
        ``connect`` is where a bad repository is reported loudly.
        """
        try:
            proc = subprocess.run(
                ["git", "-C", self._repo_path, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout

    def _to_memory(self, commit: dict) -> dict:
        """Shape one parsed commit as a memory-compatible dict."""
        sha = commit["sha"]
        short = sha[:7]
        subject = commit["subject"] or "(no subject)"

        lines = [f"{short}: {subject}"]
        body = commit["body"]
        if self._include_body and body:
            if len(body) > self._body_chars:
                body = body[: self._body_chars] + "\n... (truncated)"
            lines.append("")
            lines.append(body)

        meta: dict[str, Any] = {
            "source": "git",
            "type": "commit",
            "repo": self._repo_name,
            "sha": sha,
            "short_sha": short,
            "author": commit["author"],
            "author_email": commit["email"],
            "date": commit["date"],
            "subject": subject,
            "ref": self._ref,
        }
        if self._include_body and body:
            meta["body_chars"] = len(body)

        return {
            "content": "\n".join(lines),
            "tags": ["git", "commit", f"repo:{self._repo_name}", self._ref],
            "metadata": meta,
        }
