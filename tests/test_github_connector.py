"""GitHub connector config normalization and safety tests."""

from __future__ import annotations

import httpx
import pytest

from server.connectors.github import GitHubConnector


class _Response:
    status_code = 200
    text = "{}"


class _Client:
    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *args, **kwargs):
        self.calls.append(url)
        return _Response()


@pytest.mark.asyncio
async def test_github_connector_accepts_cli_string_config(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    conn = GitHubConnector()

    assert await conn.connect(
        {
            "token": "test-token",
            "repos": "ali-ulu/levh",
            "include_readme": "true",
            "include_issues": "false",
            "include_prs": "true",
            "include_files": "README.md,server/connectors/git.py",
            "max_issues": "12",
            "max_prs": "7",
        }
    )

    assert conn._repos == ["ali-ulu/levh"]
    assert conn._include_readme is True
    assert conn._include_issues is False
    assert conn._include_prs is True
    assert conn._include_files == ["README.md", "server/connectors/git.py"]
    assert conn._max_issues == 12
    assert conn._max_prs == 7
    assert _Client.calls[-1].endswith("/repos/ali-ulu/levh")


@pytest.mark.asyncio
async def test_github_connector_accepts_json_repo_list(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    conn = GitHubConnector()
    await conn.connect(
        {
            "token": "test-token",
            "repos": '["ali-ulu/levh", "openai/openai-python"]',
            "include_files": '["README.md", "pyproject.toml"]',
        }
    )
    assert conn._repos == ["ali-ulu/levh", "openai/openai-python"]
    assert conn._include_files == ["README.md", "pyproject.toml"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("repos", "not-a-repo"),
        ("include_files", "../secret.txt"),
        ("include_files", "docs/../secret.txt"),
        ("include_prs", "maybe"),
        ("max_issues", "many"),
    ],
)
async def test_github_connector_rejects_bad_cli_config(monkeypatch, key, value):
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    config = {"token": "test-token", "repos": "ali-ulu/levh", key: value}
    conn = GitHubConnector()
    with pytest.raises(ValueError):
        await conn.connect(config)


@pytest.mark.asyncio
async def test_github_connector_uses_environment_token(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    conn = GitHubConnector()
    assert await conn.connect({"repos": "ali-ulu/levh"})
    assert conn._token == "env-token"
