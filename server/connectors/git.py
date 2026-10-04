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
    include_file_history (bool, optional): Also emit one memory per file with
        its touch history (who touched it, how often, last change). Default
        False. Answers "whose hands has this file passed through" without the
        cost of a full blame.
    history_paths (list[str], optional): Files or directories to report history
        for, relative to the repo root. Default: the most-touched files in the
        walked commit range.
    history_max_files (int, optional): Cap on reported files (default 20).
    history_max_touches (int, optional): ``git log`` entries read per path
        (default 50).
    include_blame (bool, optional): Also emit one memory per path with the
        line-author summary (share of lines per author + most recent touch).
        Raw blame is never stored: it changes with every commit, so verbatim
        rows would churn instead of deduping. Default False.
    blame_paths (list[str], optional): Files to blame, relative to the repo
        root. Defaults to ``history_paths`` when that is given, else the
        most-touched files.
    blame_max_files (int, optional): Cap on blamed files (default 10).
    include_snapshot (bool, optional): Also emit one architecture snapshot of
        the working revision (tracked-file counts per extension, top-level
        layout, entry points), keyed by HEAD sha so re-syncing an unmoved HEAD
        dedupes instead of storing again. Default False.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
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
        self._include_history: bool = False
        self._history_paths: list[str] = []
        self._history_max_files: int = 20
        self._history_max_touches: int = 50
        self._include_blame: bool = False
        self._blame_paths: list[str] = []
        self._blame_max_files: int = 10
        self._include_snapshot: bool = False

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
        self._include_history = bool(config.get("include_file_history", False))
        self._history_paths = self._str_list(config, "history_paths")
        self._history_max_files = self._positive_int(config, "history_max_files", 20)
        self._history_max_touches = self._positive_int(
            config, "history_max_touches", 50
        )
        self._include_blame = bool(config.get("include_blame", False))
        self._blame_paths = self._str_list(config, "blame_paths")
        self._blame_max_files = self._positive_int(config, "blame_max_files", 10)
        self._include_snapshot = bool(config.get("include_snapshot", False))

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
        # History/blame paths are repo-relative and must stay inside the root:
        # an absolute path or a `..` escape would let a config read blame for a
        # file outside the repository being imported.
        self._history_paths = [self._resolve_repo_path(p) for p in self._history_paths]
        self._blame_paths = [self._resolve_repo_path(p) for p in self._blame_paths]
        return True

    def _resolve_repo_path(self, rel: str) -> str:
        """Return ``rel`` as a repo-relative posix path, or raise ValueError."""
        candidate = os.path.realpath(os.path.abspath(os.path.join(self._repo_path, rel)))
        root = os.path.realpath(os.path.abspath(self._repo_path))
        if os.path.commonpath([os.path.normcase(root), os.path.normcase(candidate)]) != os.path.normcase(root):
            raise ValueError(f"path escapes the repository root: {rel!r}")
        return os.path.relpath(candidate, root).replace(os.sep, "/")

    @staticmethod
    def _str_list(config: dict, key: str) -> list[str]:
        """Coerce a config value to a list of non-empty strings."""
        value = config.get(key, [])
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        try:
            items = [str(v).strip() for v in value]
        except TypeError:
            raise ValueError(f"{key} must be a list of strings, got {value!r}") from None
        return [v for v in items if v]

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Return one memory-compatible dict per commit, newest first.

        Plus, when enabled: one ``file_history`` memory per reported path, one
        ``blame`` memory per blamed path, and one ``arch_snapshot`` memory for
        the revision.
        """
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

        memories = [self._to_memory(c) for c in parse_git_log(raw)]

        if self._include_history or self._include_blame or self._include_snapshot:
            top_files = self._most_touched_files()
            if self._include_history:
                paths = self._history_paths or top_files[: self._history_max_files]
                for rel in paths[: self._history_max_files]:
                    mem = self._file_history_memory(rel)
                    if mem is not None:
                        memories.append(mem)
            if self._include_blame:
                paths = (
                    self._blame_paths
                    or self._history_paths
                    or top_files[: self._blame_max_files]
                )
                for rel in paths[: self._blame_max_files]:
                    mem = self._blame_memory(rel)
                    if mem is not None:
                        memories.append(mem)
            if self._include_snapshot:
                mem = self._snapshot_memory()
                if mem is not None:
                    memories.append(mem)

        return memories

    async def disconnect(self) -> None:
        self._repo_path = ""
        self._repo_name = ""
        self._history_paths = []
        self._blame_paths = []

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

    def _most_touched_files(self) -> list[str]:
        """Repo-relative paths ordered by touch count in the walked range."""
        raw = self._run_git(
            [
                "log",
                f"--max-count={self._max_commits}",
                "--pretty=format:",
                "--name-only",
                self._ref,
                "--",
            ]
        )
        if not raw:
            return []
        counts: Counter[str] = Counter()
        for line in raw.splitlines():
            name = line.strip()
            if name:
                counts[name] += 1
        return [name for name, _ in counts.most_common()]

    def _file_history_memory(self, rel: str) -> dict | None:
        """One memory: who touched ``rel``, how often, and last."""
        raw = self._run_git(
            [
                "log",
                f"--max-count={self._history_max_touches}",
                "--format=%H|%an|%aI|%s",
                self._ref,
                "--",
                rel,
            ]
        )
        if not raw:
            return None
        touches = 0
        authors: Counter[str] = Counter()
        last_sha = last_author = last_date = last_subject = ""
        for line in raw.splitlines():
            parts = line.split("|", 3)
            if len(parts) < 4 or not parts[0].strip():
                continue
            sha, author, date, subject = (p.strip() for p in parts)
            touches += 1
            authors[author or "(unknown)"] += 1
            if not last_sha:
                last_sha, last_author, last_date, last_subject = (
                    sha,
                    author,
                    date,
                    subject,
                )
        if not touches:
            return None
        top = ", ".join(f"{a} ({n})" for a, n in authors.most_common(5))
        content = (
            f"History: {rel} ({touches} touches)\n"
            f"Last: {last_sha[:7]} {last_subject or '(no subject)'}"
            f" by {last_author or '(unknown)'} on {last_date}\n"
            f"Authors: {top}"
        )
        return {
            "content": content,
            "tags": ["git", "file-history", f"repo:{self._repo_name}", self._ref],
            "metadata": {
                "source": "git",
                "type": "file_history",
                "repo": self._repo_name,
                "path": rel,
                "ref": self._ref,
                "touches": touches,
                "authors": dict(authors.most_common(10)),
                "last_sha": last_sha,
                "last_author": last_author,
                "last_date": last_date,
            },
        }

    def _newest_rank(self, rel: str) -> dict[str, int]:
        """Map commit sha to its position in newest-first log order for ``rel``.

        Blame blocks carry ``author-time`` with one-second resolution, so two
        commits landed in the same second tie. The log order breaks the tie:
        a smaller index is the newer commit.
        """
        raw = self._run_git(
            [
                "log",
                f"--max-count={self._history_max_touches}",
                "--format=%H",
                self._ref,
                "--",
                rel,
            ]
        )
        if not raw:
            return {}
        return {
            sha.strip(): index
            for index, sha in enumerate(raw.splitlines())
            if sha.strip()
        }

    def _blame_memory(self, rel: str) -> dict | None:
        """One memory: the line-author summary of ``rel`` at the revision."""
        raw = self._run_git(["blame", "--line-porcelain", self._ref, "--", rel])
        if not raw:
            return None
        rank = self._newest_rank(rel)
        lines_per_author: Counter[str] = Counter()
        total = 0
        latest_key: tuple[int, int] = (-1, 0)
        latest_author = latest_sha = ""
        author = ""
        sha = ""
        block_lines = 0
        author_time = -1
        for line in raw.splitlines():
            if line.startswith("\t"):
                if author:
                    lines_per_author[author] += block_lines
                    total += block_lines
                    # Newer commit wins; on equal timestamps the log order
                    # (smaller rank = newer) breaks the tie. Unknown shas
                    # sort below every ranked one.
                    key = (author_time, -rank.get(sha, 1_000_000))
                    if key > latest_key:
                        latest_key = key
                        latest_author = author
                        latest_sha = sha
                author, sha, block_lines, author_time = "", "", 0, -1
                continue
            if line and line[0] in "0123456789abcdef" and len(line.split()) == 4:
                parts = line.split()
                if len(parts[0]) == 40:
                    sha = parts[0]
                    try:
                        block_lines = int(parts[3])
                    except ValueError:
                        block_lines = 0
            elif line.startswith("author ") and not line.startswith("author-"):
                author = line[len("author "):].strip() or "(unknown)"
            elif line.startswith("author-time "):
                try:
                    author_time = int(line.split()[-1])
                except ValueError:
                    author_time = -1
        if not total:
            return None
        ranked = lines_per_author.most_common(5)
        shares = ", ".join(
            f"{a} ({n} lines, {n * 100 // total}%)" for a, n in ranked
        )
        content = (
            f"Blame: {rel} ({total} lines at {self._ref})\n"
            f"Latest touch: {latest_sha[:7]} by {latest_author}\n"
            f"Line share: {shares}"
        )
        return {
            "content": content,
            "tags": ["git", "blame", f"repo:{self._repo_name}", self._ref],
            "metadata": {
                "source": "git",
                "type": "blame",
                "repo": self._repo_name,
                "path": rel,
                "ref": self._ref,
                "total_lines": total,
                "lines_per_author": dict(lines_per_author.most_common(10)),
                "latest_author": latest_author,
                "latest_sha": latest_sha,
            },
        }

    # Entry-point filenames worth surfacing in a snapshot. Curated, not
    # exhaustive: the snapshot answers "where do I start reading", and a
    # hundred-name table would answer it worse.
    _ENTRY_POINTS = {
        "README.md",
        "readme.md",
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Makefile",
        "Dockerfile",
        "main.py",
        "app.py",
        "server.py",
        "index.js",
        "index.ts",
    }

    def _snapshot_memory(self) -> dict | None:
        """One memory: the tracked-tree layout of the revision."""
        head = self._run_git(["rev-parse", self._ref])
        if head is None:
            return None
        head = head.strip().splitlines()[0]
        raw = self._run_git(["ls-files"])
        if raw is None:
            return None
        files = [ln for ln in raw.splitlines() if ln.strip()]
        ext_counts: Counter[str] = Counter()
        top_dirs: Counter[str] = Counter()
        entry: list[str] = []
        for name in files:
            _, dot, ext = name.rpartition(".")
            ext_counts[("." + ext.lower()) if dot and "/" not in ext else "(noext)"] += 1
            top_dirs[name.split("/", 1)[0] if "/" in name else "(root)"] += 1
            if name.split("/")[-1] in self._ENTRY_POINTS and name not in entry:
                entry.append(name)
        ext_line = ", ".join(
            f"{e} {n}" for e, n in ext_counts.most_common(8)
        )
        dir_line = ", ".join(
            f"{d}/ ({n})" for d, n in top_dirs.most_common(10)
        )
        lines = [
            f"Snapshot: {self._repo_name} @ {head[:7]} ({len(files)} tracked files)",
            f"By extension: {ext_line}",
            f"Top level: {dir_line}",
        ]
        if entry:
            lines.append(f"Entry points: {', '.join(sorted(entry))}")
        return {
            "content": "\n".join(lines),
            "tags": ["git", "arch-snapshot", f"repo:{self._repo_name}"],
            "metadata": {
                "source": "git",
                "type": "arch_snapshot",
                "repo": self._repo_name,
                "ref": self._ref,
                "sha": head,
                "tracked_files": len(files),
                "by_extension": dict(ext_counts.most_common(20)),
                "entry_points": sorted(entry),
            },
        }
