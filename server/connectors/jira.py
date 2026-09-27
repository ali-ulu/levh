"""Jira Connector — Pull issues from Jira Cloud via REST API v3.

Uses the Jira Cloud REST API directly with httpx (no SDK dependency).

Config keys:
    base_url (str): Site URL, e.g. ``https://your-team.atlassian.net``.
    email (str): Atlassian account email that owns the API token.
    api_token (str): Jira API token (can also use ``JIRA_API_TOKEN`` env).
    jql (str, optional): JQL filter. Defaults to everything updated in the
        last 90 days, which keeps a first sync bounded on a large site.
    max_issues (int, optional): Max issues to fetch. Default 100.
    include_comments (bool, optional): Fetch each issue's comments. Default False.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from .base import BaseConnector

# Jira Cloud REST API v3. The newer search endpoints
# (/rest/api/3/search/jql) replaced the legacy GET /rest/api/3/search, which
# Atlassian has deprecated; use the current one.
DEFAULT_JQL = "updated >= -90d ORDER BY updated DESC"


class JiraConnector(BaseConnector):
    """Import issues from Jira Cloud."""

    name: str = "jira"
    description: str = (
        "Import issues from Jira Cloud via REST API v3. "
        "Requires a site URL, an account email, and an API token "
        "(JIRA_API_TOKEN)."
    )

    def __init__(self) -> None:
        self._base_url: str = ""
        self._headers: dict[str, str] = {}
        self._jql: str = DEFAULT_JQL
        self._max_issues: int = 100
        self._include_comments: bool = False

    def required_config_keys(self) -> list[str]:
        return ["base_url", "email", "api_token"]

    async def connect(self, config: dict) -> bool:
        """Validate the Jira credentials.

        Config keys:
            base_url (str): Site URL.
            email (str): Atlassian account email.
            api_token (str, optional): API token; falls back to JIRA_API_TOKEN.
            jql (str, optional): JQL filter.
            max_issues (int, optional): Max issues. Default 100.
            include_comments (bool, optional): Fetch comments. Default False.
        """
        base_url = (config.get("base_url", "") or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        email = config.get("email", "") or os.getenv("JIRA_EMAIL", "")
        api_token = config.get("api_token", "") or os.getenv("JIRA_API_TOKEN", "")

        missing = [
            key
            for key, value in (("base_url", base_url), ("email", email), ("api_token", api_token))
            if not value
        ]
        if missing:
            raise ValueError(
                f"Jira config is missing: {', '.join(missing)}. "
                "Pass them via config or set JIRA_BASE_URL / JIRA_EMAIL / "
                "JIRA_API_TOKEN."
            )

        basic = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._base_url = base_url
        self._headers = {
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._jql = config.get("jql", DEFAULT_JQL)
        self._max_issues = config.get("max_issues", 100)
        self._include_comments = config.get("include_comments", False)

        # A cheap call that proves the token belongs to a real account; a
        # successful issue search would be a heavier way to learn the same thing.
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            resp = await client.get(f"{self._base_url}/rest/api/3/myself", headers=self._headers)
            if resp.status_code != 200:
                raise ConnectionError(
                    f"Jira API returned {resp.status_code} for {self._base_url}/rest/api/3/myself: "
                    f"{resp.text[:200]}"
                )
        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch issues from Jira and return them as memory dicts."""
        import httpx

        memories: list[dict] = []
        # ADF (Atlassian Document Format) is the default body format; the plain
        # "renderedFields" variant is requested so the body arrives as HTML-ish
        # text rather than a nested JSON tree we would have to walk.
        fields = "summary,description,status,priority,labels,assignee,reporter,created,updated,issuetype"
        params = {
            "jql": self._jql,
            "maxResults": min(self._max_issues, 100),
            "fields": fields,
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0, pool=10.0)
        ) as client:
            start_at = 0
            while len(memories) < self._max_issues:
                params["startAt"] = start_at
                try:
                    resp = await client.get(
                        f"{self._base_url}/rest/api/3/search/jql",
                        headers=self._headers,
                        params=params,
                    )
                except httpx.HTTPError:
                    break
                if resp.status_code != 200:
                    break

                data = resp.json()
                issues = data.get("issues", [])
                if not issues:
                    break

                for issue in issues:
                    if len(memories) >= self._max_issues:
                        break
                    memory = self._to_memory(issue)
                    if self._include_comments:
                        comments = await self._fetch_comments(client, issue.get("key", ""))
                        if comments:
                            memory["content"] = f"{memory['content']}\n\n{comments}"
                    memories.append(memory)

                if len(memories) >= self._max_issues:
                    break

                total = data.get("total")
                start_at += len(issues)
                # `total` is absent on some endpoints; stop on a short page.
                if total is not None and start_at >= total:
                    break
                if len(issues) < params["maxResults"]:
                    break

        return memories

    def _to_memory(self, issue: dict) -> dict:
        """Convert a Jira issue payload into a memory dict."""
        key = issue.get("key", "")
        fields = issue.get("fields", {}) or {}
        summary = fields.get("summary", "") or ""
        description = self._flatten_description(fields.get("description"))

        labels = fields.get("labels", []) or []
        status = (fields.get("status") or {}).get("name", "")
        priority = (fields.get("priority") or {}).get("name", "")
        issue_type = (fields.get("issuetype") or {}).get("name", "")
        assignee = (fields.get("assignee") or {}).get("displayName", "")
        reporter = (fields.get("reporter") or {}).get("displayName", "")

        # Header first, because a memory is recalled by its opening words; the
        # key and status are what make an issue searchable later.
        lines = [f"{key}: {summary}".strip(": ")]
        if status or issue_type:
            lines.append(f"Status: {status} · Type: {issue_type}")
        if assignee or priority:
            lines.append(f"Assignee: {assignee or 'unassigned'} · Priority: {priority or 'none'}")
        if description:
            lines.append("")
            lines.append(description)
        content = "\n".join(lines).strip()

        tags = ["jira", "issue"] + [str(label) for label in labels]
        if issue_type:
            tags.append(f"type:{issue_type}")
        if status:
            tags.append(f"status:{status}")

        return {
            "content": content[:8000],
            "tags": tags,
            "metadata": {
                "source": "jira",
                "type": "issue",
                "issue_key": key,
                "url": f"{self._base_url}/browse/{key}" if key else "",
                "state": status,
                "priority": priority,
                "issue_type": issue_type,
                "assignee": assignee,
                "reporter": reporter,
                "labels": labels,
                "created_at": fields.get("created", ""),
                "updated_at": fields.get("updated", ""),
            },
        }

    @staticmethod
    def _flatten_description(description: Any) -> str:
        """Render a Jira description to text.

        Jira Cloud sends Atlassian Document Format — a nested
        ``{type, content: [...]}`` tree — unless configured otherwise. Walking
        it for the ``text`` leaves is enough to make the body searchable and
        avoids storing a JSON blob nothing can read back.
        """
        if description is None:
            return ""
        if isinstance(description, str):
            return description

        parts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    parts.append(node["text"])
                for child in node.get("content", []) or []:
                    walk(child)
                if node.get("type") in {"paragraph", "heading", "listItem"}:
                    parts.append("\n")
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(description)
        return "".join(parts).strip()

    async def _fetch_comments(self, client: Any, issue_key: str) -> str:
        """Return an issue's comments as a text block, or an empty string."""
        import httpx

        if not issue_key:
            return ""
        try:
            resp = await client.get(
                f"{self._base_url}/rest/api/3/issue/{issue_key}/comment",
                headers=self._headers,
            )
        except httpx.HTTPError:
            return ""
        if resp.status_code != 200:
            return ""

        lines: list[str] = []
        for comment in resp.json().get("comments", []) or []:
            author = (comment.get("author") or {}).get("displayName", "unknown")
            body = self._flatten_description(comment.get("body"))
            if body:
                lines.append(f"{author}: {body}")
        return "\n".join(lines)

    async def disconnect(self) -> None:
        """Nothing to release: every request uses a short-lived client."""
        self._headers = {}
