"""GitHub Connector — Pull data from GitHub repositories.

Fetches README, issues, pull requests, and code files from one or more
repos and converts them to memories.

Config keys:
    token (str): GitHub personal access token. Can also use ``GITHUB_TOKEN`` env.
    repos (list[str]): List of repos as ``owner/repo`` (e.g. ``["anthropics/claude-code"]``).
    include_readme (bool, optional): Fetch README. Default True.
    include_issues (bool, optional): Fetch open issues. Default True.
    include_prs (bool, optional): Fetch open PRs. Default False.
    include_files (list[str], optional): Specific file paths to fetch from each repo.
    max_issues (int, optional): Max issues per repo. Default 50.
    max_prs (int, optional): Max PRs per repo. Default 20.
"""

from __future__ import annotations

import os
from typing import Any

from .base import BaseConnector

# GitHub API base
GITHUB_API = "https://api.github.com"


class GitHubConnector(BaseConnector):
    """Import data from GitHub repositories."""

    name: str = "github"
    description: str = (
        "Import README, issues, PRs, and code files from GitHub repos. "
        "Requires a GitHub personal access token (GITHUB_TOKEN)."
    )

    def __init__(self) -> None:
        self._token: str = ""
        self._headers: dict[str, str] = {}
        self._repos: list[str] = []
        self._include_readme: bool = True
        self._include_issues: bool = True
        self._include_prs: bool = False
        self._include_files: list[str] = []
        self._max_issues: int = 50
        self._max_prs: int = 20

    def required_config_keys(self) -> list[str]:
        return ["token", "repos"]

    async def connect(self, config: dict) -> bool:
        """Validate the GitHub token.

        Config keys:
            token (str): GitHub PAT.
            repos (list[str]): ``[\"owner/repo\", ...]``
            include_readme (bool): Default True.
            include_issues (bool): Default True.
            include_prs (bool): Default False.
            include_files (list[str]): File paths to fetch.
            max_issues (int): Default 50.
            max_prs (int): Default 20.
        """
        token = config.get("token", "") or os.getenv("GITHUB_TOKEN", "")
        if not token:
            raise ValueError(
                "GitHub token is required. "
                "Pass it via config['token'] or set GITHUB_TOKEN env var."
            )

        self._token = token
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        repos = config.get("repos", [])
        if not repos:
            raise ValueError("At least one repo is required (format: 'owner/repo').")
        self._repos = repos

        self._include_readme = config.get("include_readme", True)
        self._include_issues = config.get("include_issues", True)
        self._include_prs = config.get("include_prs", False)
        self._include_files = config.get("include_files", [])
        self._max_issues = config.get("max_issues", 50)
        self._max_prs = config.get("max_prs", 20)

        # Quick validation
        import httpx
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            resp = await client.get(
                f"{GITHUB_API}/user",
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code != 200:
                raise ConnectionError(
                    f"GitHub API returned {resp.status_code}: {resp.text[:200]}"
                )

        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch data from all configured repos."""
        import httpx

        memories: list[dict] = []

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            for repo in self._repos:
                repo_memories = await self._fetch_repo(client, repo)
                memories.extend(repo_memories)

        return memories

    async def disconnect(self) -> None:
        self._token = ""
        self._headers = {}

    # ── Internal helpers ───────────────────────────────────────────

    async def _fetch_repo(self, client: Any, repo: str) -> list[dict]:
        """Fetch all configured data from a single repo."""
        memories: list[dict] = []

        if self._include_readme:
            readme = await self._fetch_readme(client, repo)
            if readme:
                memories.append(readme)

        if self._include_issues:
            issues = await self._fetch_issues(client, repo)
            memories.extend(issues)

        if self._include_prs:
            prs = await self._fetch_prs(client, repo)
            memories.extend(prs)

        for file_path in self._include_files:
            file_mem = await self._fetch_file(client, repo, file_path)
            if file_mem:
                memories.append(file_mem)

        return memories

    async def _fetch_readme(self, client: Any, repo: str) -> dict | None:
        """Fetch the README.md from a repo."""
        import httpx

        try:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/readme",
                headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            content = resp.text
        except httpx.HTTPError:
            return None

        return {
            "content": content[:8000],
            "tags": ["github", "readme", f"repo:{repo}"],
            "metadata": {
                "source": "github",
                "repo": repo,
                "type": "readme",
            },
        }

    async def _fetch_issues(self, client: Any, repo: str) -> list[dict]:
        """Fetch open issues from a repo."""
        import httpx

        issues: list[dict] = []
        try:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=self._headers,
                params={"state": "open", "per_page": self._max_issues},
                timeout=30,
            )
            if resp.status_code != 200:
                return issues
            data = resp.json()
        except httpx.HTTPError:
            return issues

        for issue in data:
            # Skip PRs (they show up in issues API)
            if "pull_request" in issue:
                continue

            title = issue.get("title", "")
            body = issue.get("body", "") or ""
            labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]

            content = f"Issue #{issue.get('number', '?')}: {title}\n\n{body}".strip()

            tags = ["github", "issue", f"repo:{repo}"] + labels
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"

            issues.append(
                {
                    "content": content,
                    "tags": tags,
                    "metadata": {
                        "source": "github",
                        "repo": repo,
                        "type": "issue",
                        "issue_number": issue.get("number"),
                        "url": issue.get("html_url", ""),
                        "state": issue.get("state", ""),
                        "labels": labels,
                        "created_at": issue.get("created_at", ""),
                    },
                }
            )

        return issues

    async def _fetch_prs(self, client: Any, repo: str) -> list[dict]:
        """Fetch open pull requests from a repo."""
        import httpx

        prs: list[dict] = []
        try:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/pulls",
                headers=self._headers,
                params={"state": "open", "per_page": self._max_prs},
                timeout=30,
            )
            if resp.status_code != 200:
                return prs
            data = resp.json()
        except httpx.HTTPError:
            return prs

        for pr in data:
            title = pr.get("title", "")
            body = pr.get("body", "") or ""
            labels = [lbl.get("name", "") for lbl in pr.get("labels", [])]

            content = f"PR #{pr.get('number', '?')}: {title}\n\n{body}".strip()

            tags = ["github", "pull-request", f"repo:{repo}"] + labels
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"

            prs.append({
                "content": content,
                "tags": tags,
                "metadata": {
                    "source": "github",
                    "repo": repo,
                    "type": "pull_request",
                    "pr_number": pr.get("number"),
                    "url": pr.get("html_url", ""),
                    "state": pr.get("state", ""),
                    "author": pr.get("user", {}).get("login", ""),
                    "labels": labels,
                    "created_at": pr.get("created_at", ""),
                },
            })

        return prs

    async def _fetch_file(
        self, client: Any, repo: str, file_path: str
    ) -> dict | None:
        """Fetch a single file from a repo."""
        import httpx

        try:
            resp = await client.get(
                f"{GITHUB_API}/repos/{repo}/contents/{file_path}",
                headers={**self._headers, "Accept": "application/vnd.github.raw+json"},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            content = resp.text
        except httpx.HTTPError:
            return None

        ext = os.path.splitext(file_path)[1].lower()
        tags = ["github", "code", f"repo:{repo}", ext.lstrip(".")]

        return {
            "content": content[:6000],
            "tags": tags,
            "metadata": {
                "source": "github",
                "repo": repo,
                "type": "file",
                "file_path": file_path,
            },
        }