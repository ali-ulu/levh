"""Tests for the Git connector — repository history as memories.
Offline: every fixture is a real temporary git repository built in the test.
"""

import os
import subprocess
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["EMBEDDER_MODE"] = "hash"
os.environ.pop("OPENAI_API_KEY", None)

from server.connectors import get_connector, list_connectors
from server.connectors.git import (
    _FIELD_SEP,
    _RECORD_SEP,
    GitConnector,
    parse_git_log,
)
from server.core.memory_engine import MemoryEngine


# ── real-repository fixture helper ─────────────────────────────────


def _git(repo: str, *args: str) -> str:
    """Run one git command in ``repo`` and return stdout."""
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=False,
        check=True,
    )
    return proc.stdout


def _make_repo(*commits: tuple[str, str]) -> str:
    """Build a temporary repository with one commit per ``(subject, body)``.

    Returns the repo path. The caller owns cleanup.
    """
    repo = tempfile.mkdtemp()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Ali Ulu")
    _git(repo, "config", "user.email", "ali@example.com")
    # A deterministic identity keeps author assertions stable regardless of
    # the machine's global git config.
    for index, (subject, body) in enumerate(commits):
        path = os.path.join(repo, f"file{index}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{subject}\n")
        _git(repo, "add", f"file{index}.txt")
        args = ["commit", "-q", "-m", subject]
        if body:
            args += ["-m", body]
        _git(repo, *args)
    return repo


def _cleanup(repo: str) -> None:
    import shutil

    shutil.rmtree(repo, ignore_errors=True)


# ── parser (unit) ──────────────────────────────────────────────────


def test_parse_single_record():
    raw = _FIELD_SEP.join(
        ["a" * 40, "Ali Ulu", "ali@example.com", "2026-10-02T12:00:00+03:00",
         "fix: the thing", "Longer explanation."]
    ) + _RECORD_SEP
    out = parse_git_log(raw)
    assert len(out) == 1
    assert out[0]["sha"] == "a" * 40
    assert out[0]["author"] == "Ali Ulu"
    assert out[0]["email"] == "ali@example.com"
    assert out[0]["subject"] == "fix: the thing"
    assert out[0]["body"] == "Longer explanation."


def test_parse_multiple_records_keeps_order():
    def rec(sha: str, subject: str) -> str:
        return _FIELD_SEP.join([sha, "A", "a@b.c", "2026-01-01T00:00:00+00:00", subject, ""]) + _RECORD_SEP

    out = parse_git_log(rec("1" * 40, "first") + rec("2" * 40, "second"))
    assert [c["subject"] for c in out] == ["first", "second"]


def test_parse_empty_and_malformed_are_skipped():
    assert parse_git_log("") == []
    assert parse_git_log(_RECORD_SEP) == []
    # Too few fields for a commit — dropped, not an exception.
    assert parse_git_log("only\x1ftwo" + _RECORD_SEP) == []


def test_parse_body_containing_newlines_and_separators():
    """A body with newlines survives; the record separator is the boundary."""
    body = "line one\nline two\n\nline four"
    raw = _FIELD_SEP.join(
        ["b" * 40, "A", "a@b.c", "2026-01-01T00:00:00+00:00", "subj", body]
    ) + _RECORD_SEP
    out = parse_git_log(raw)
    assert out[0]["body"] == body


# ── connector (fetch) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_fetch_one_memory_per_commit():
    repo = _make_repo(("feat: add memory", "Why we added it."))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo})
        items = await conn.fetch()
        assert len(items) == 1
        mem = items[0]
        assert mem["content"].startswith(items[0]["metadata"]["short_sha"])
        assert "feat: add memory" in mem["content"]
        assert "Why we added it." in mem["content"]
        assert mem["tags"] == ["git", "commit", f"repo:{os.path.basename(repo)}", "HEAD"]
        assert mem["metadata"]["source"] == "git"
        assert mem["metadata"]["author"] == "Ali Ulu"
        assert mem["metadata"]["author_email"] == "ali@example.com"
        assert len(mem["metadata"]["sha"]) == 40
        assert mem["metadata"]["subject"] == "feat: add memory"
        await conn.disconnect()
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_fetch_newest_first():
    repo = _make_repo(("first commit", ""), ("second commit", ""))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo})
        items = await conn.fetch()
        assert [m["metadata"]["subject"] for m in items] == ["second commit", "first commit"]
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_max_commits_caps_output():
    repo = _make_repo(("c1", ""), ("c2", ""), ("c3", ""))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo, "max_commits": 2})
        items = await conn.fetch()
        assert [m["metadata"]["subject"] for m in items] == ["c3", "c2"]
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_include_body_false_omits_body():
    repo = _make_repo(("subject line", "body text here"))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo, "include_body": False})
        items = await conn.fetch()
        assert "body text here" not in items[0]["content"]
        assert "body_chars" not in items[0]["metadata"]
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_body_chars_truncates():
    long_body = "x" * 300
    repo = _make_repo(("s", long_body))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo, "body_chars": 20})
        items = await conn.fetch()
        assert "... (truncated)" in items[0]["content"]
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_author_filter():
    repo = tempfile.mkdtemp()
    try:
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Ali Ulu")
        _git(repo, "config", "user.email", "ali@example.com")
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("a")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "by ali")

        _git(repo, "config", "user.name", "Dana Lee")
        _git(repo, "config", "user.email", "dana@example.com")
        with open(os.path.join(repo, "b.txt"), "w") as fh:
            fh.write("b")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-q", "-m", "by dana")

        conn = GitConnector()
        await conn.connect({"repo_path": repo, "author": "Dana"})
        items = await conn.fetch()
        assert [m["metadata"]["subject"] for m in items] == ["by dana"]
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_subdirectory_is_refused_with_the_root_named():
    """A path inside the repo is refused, and the error names the real root.

    The connector deliberately requires the repository root. Accepting a
    subdirectory would also accept a directory that merely sits beneath an
    unrelated enclosing repository — git walks upward identically in both cases.
    """
    repo = _make_repo(("root commit", ""))
    sub = os.path.join(repo, "nested")
    os.makedirs(sub)
    try:
        conn = GitConnector()
        with pytest.raises(ConnectionError) as excinfo:
            await conn.connect({"repo_path": sub})
        # The message must hand the caller the path that actually works.
        assert os.path.realpath(repo) in str(excinfo.value)
    finally:
        _cleanup(repo)


# ── robustness / security ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_connect_rejects_empty_and_missing_path():
    conn = GitConnector()
    with pytest.raises(ValueError):
        await conn.connect({})
    with pytest.raises(FileNotFoundError):
        await conn.connect({"repo_path": os.path.join(tempfile.gettempdir(), "definitely-not-here-xyz")})


@pytest.mark.asyncio
async def test_git_connect_rejects_non_repository():
    """A plain directory is refused.

    Fresh tempdir on purpose: pytest's ``tmp_path`` can itself live under a git
    working tree, where ``git rev-parse`` walks upward and succeeds.
    """
    plain = tempfile.mkdtemp()
    try:
        conn = GitConnector()
        with pytest.raises(ConnectionError):
            await conn.connect({"repo_path": plain})
    finally:
        _cleanup(plain)


@pytest.mark.asyncio
async def test_git_connect_rejects_sibling_of_repo_not_importer_of_it():
    """A directory beside a repo must not import that repo's history.

    Regression test. ``git rev-parse`` reports success from any directory that
    merely sits inside *some* repository's tree, so an unrelated path would
    silently import the outer repository. This machine makes the hazard concrete:
    the user's home directory is itself a git repo, so every temp directory
    beneath it resolves to an enclosing repository.
    """
    repo = _make_repo(("outer commit", ""))
    sibling = tempfile.mkdtemp()
    try:
        conn = GitConnector()
        with pytest.raises(ConnectionError):
            await conn.connect({"repo_path": sibling})
    finally:
        _cleanup(repo)
        _cleanup(sibling)


@pytest.mark.asyncio
async def test_git_connect_rejects_option_like_ref():
    """A ref that git would read as an option is refused up front."""
    repo = _make_repo(("only commit", ""))
    try:
        conn = GitConnector()
        with pytest.raises(ValueError):
            await conn.connect({"repo_path": repo, "ref": "--upload-pack=evil"})
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_connect_rejects_bad_numeric_option():
    repo = _make_repo(("c", ""))
    try:
        conn = GitConnector()
        with pytest.raises(ValueError):
            await conn.connect({"repo_path": repo, "max_commits": "not-a-number"})
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_bad_ref_yields_no_commits_not_exception():
    """fetch() degrades to an empty list rather than raising mid-import."""
    repo = _make_repo(("c", ""))
    try:
        conn = GitConnector()
        await conn.connect({"repo_path": repo, "ref": "no-such-branch-xyz"})
        assert await conn.fetch() == []
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_empty_repository_yields_no_commits():
    repo = tempfile.mkdtemp()
    try:
        _git(repo, "init", "-q")
        conn = GitConnector()
        await conn.connect({"repo_path": repo})
        assert await conn.fetch() == []
    finally:
        _cleanup(repo)


@pytest.mark.asyncio
async def test_git_fetch_before_connect_raises():
    conn = GitConnector()
    with pytest.raises(RuntimeError):
        await conn.fetch()


# ── registry ───────────────────────────────────────────────────────


def test_git_in_registry():
    names = {c["name"] for c in list_connectors()}
    assert "git" in names
    conn = get_connector("git")
    assert conn.name == "git"
    assert conn.required_config_keys() == ["repo_path"]
    assert "read-only" in conn.help_text().lower()


# ── end-to-end via API ─────────────────────────────────────────────


@pytest_asyncio.fixture
async def api_client():
    from httpx import ASGITransport, AsyncClient

    import server.api as api_mod

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    if api_mod._engine is not None:
        await api_mod._engine.shutdown()
    api_mod._engine = MemoryEngine(db_path=db_path, embedder_mode="hash", short_term_max=50)
    await api_mod._engine.initialize()
    api_mod._initialized = True
    transport = ASGITransport(app=api_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await api_mod._engine.shutdown()
    api_mod._engine = None
    api_mod._initialized = False
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.mark.asyncio
async def test_api_git_import_and_recall(api_client):
    repo = _make_repo(("fix: correct the git connector", "Because pathspecs."))
    try:
        r = await api_client.post(
            "/api/connectors/import",
            json={"connector": "git", "config": {"repo_path": repo}, "project": "levh"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["stored"] == 1

        r = await api_client.get("/api/memories", params={"source": "connector:git"})
        mems = r.json()
        assert len(mems) == 1
        assert mems[0]["project"] == "levh"
        assert "git" in mems[0]["tags"]
    finally:
        _cleanup(repo)
