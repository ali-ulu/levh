"""Linear Connector — Pull issues from Linear via its GraphQL API.

Uses Linear's GraphQL endpoint directly with httpx (no SDK dependency).

Config keys:
    api_key (str): Linear personal API key (can also use ``LINEAR_API_KEY`` env).
    team_ids (list[str], optional): Restrict to these team IDs.
    project_ids (list[str], optional): Restrict to these project IDs.
    max_issues (int, optional): Max issues to fetch. Default 100.
    include_comments (bool, optional): Fetch each issue's comments. Default False.
"""

from __future__ import annotations

import os
from typing import Any

from .base import BaseConnector

LINEAR_API = "https://api.linear.app/graphql"

# One page of issues per request; Linear pages by cursor.
_PAGE_SIZE = 50

_ISSUES_QUERY = """
query Issues($first: Int!, $after: String, $filter: IssueFilter) {
  issues(first: $first, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      identifier
      title
      description
      url
      priorityLabel
      createdAt
      updatedAt
      state { name }
      assignee { displayName }
      team { id key name }
      project { id name }
      labels(first: 20) { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_COMMENTS_QUERY = """
query Comments($issueId: String!, $first: Int!) {
  issue(id: $issueId) {
    comments(first: $first) {
      nodes { body user { displayName } }
    }
  }
}
"""


class LinearConnector(BaseConnector):
    """Import issues from Linear."""

    name: str = "linear"
    description: str = (
        "Import issues from Linear via its GraphQL API. "
        "Requires a Linear personal API key (LINEAR_API_KEY)."
    )

    def __init__(self) -> None:
        self._api_key: str = ""
        self._headers: dict[str, str] = {}
        self._filter: dict[str, Any] | None = None
        self._max_issues: int = 100
        self._include_comments: bool = False

    def required_config_keys(self) -> list[str]:
        return ["api_key"]

    async def connect(self, config: dict) -> bool:
        """Validate the Linear API key with a viewer query.

        Config keys:
            api_key (str, optional): Personal API key; falls back to LINEAR_API_KEY.
            team_ids (list[str], optional): Restrict to these teams.
            project_ids (list[str], optional): Restrict to these projects.
            max_issues (int, optional): Max issues. Default 100.
            include_comments (bool, optional): Fetch comments. Default False.
        """
        api_key = config.get("api_key", "") or os.getenv("LINEAR_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Linear API key is required. "
                "Pass it via config['api_key'] or set LINEAR_API_KEY."
            )

        self._api_key = api_key
        self._headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
        self._max_issues = config.get("max_issues", 100)
        self._include_comments = config.get("include_comments", False)
        self._filter = self._build_filter(config)

        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            resp = await client.post(
                LINEAR_API,
                headers=self._headers,
                json={"query": "query { viewer { id name } }"},
            )
            if resp.status_code != 200:
                raise ConnectionError(
                    f"Linear API returned {resp.status_code}: {resp.text[:200]}"
                )
            payload = resp.json()
            if payload.get("errors"):
                raise ConnectionError(
                    f"Linear API rejected the query: {str(payload['errors'])[:200]}"
                )
            # A key that authenticates but has no viewer is not a usable key.
            if not (payload.get("data") or {}).get("viewer"):
                raise ConnectionError("Linear API returned no viewer for this API key")
        return True

    @staticmethod
    def _build_filter(config: dict) -> dict[str, Any] | None:
        """Translate team/project ids into a Linear IssueFilter, if given."""
        conditions: dict[str, Any] = {}
        team_ids = config.get("team_ids") or []
        project_ids = config.get("project_ids") or []
        if team_ids:
            conditions["team"] = {"id": {"in": team_ids}}
        if project_ids:
            conditions["project"] = {"id": {"in": project_ids}}
        return conditions or None

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch issues from Linear and return them as memory dicts."""
        import httpx

        memories: list[dict] = []
        cursor: str | None = None

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0, pool=10.0)
        ) as client:
            while len(memories) < self._max_issues:
                variables: dict[str, Any] = {
                    "first": min(_PAGE_SIZE, self._max_issues - len(memories)),
                    "after": cursor,
                }
                if self._filter:
                    variables["filter"] = self._filter

                try:
                    resp = await client.post(
                        LINEAR_API,
                        headers=self._headers,
                        json={"query": _ISSUES_QUERY, "variables": variables},
                    )
                except httpx.HTTPError:
                    break
                if resp.status_code != 200:
                    break

                payload = resp.json()
                if payload.get("errors"):
                    break

                connection = ((payload.get("data") or {}).get("issues")) or {}
                nodes = connection.get("nodes") or []
                if not nodes:
                    break

                for node in nodes:
                    memory = self._to_memory(node)
                    if self._include_comments:
                        comments = await self._fetch_comments(client, node.get("identifier", ""))
                        if comments:
                            memory["content"] = f"{memory['content']}\n\n{comments}"
                    memories.append(memory)
                    if len(memories) >= self._max_issues:
                        break

                page_info = connection.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
                if not cursor:
                    break

        return memories

    def _to_memory(self, issue: dict) -> dict:
        """Convert a Linear issue node into a memory dict."""
        identifier = issue.get("identifier", "")
        title = issue.get("title", "") or ""
        description = issue.get("description", "") or ""

        state = (issue.get("state") or {}).get("name", "")
        assignee = (issue.get("assignee") or {}).get("displayName", "")
        team = issue.get("team") or {}
        project = issue.get("project") or {}
        labels = [
            node.get("name", "")
            for node in ((issue.get("labels") or {}).get("nodes") or [])
            if node.get("name")
        ]

        # Header first: a memory is recalled by its opening words, and the
        # identifier is what makes an issue findable by name later.
        lines = [f"{identifier}: {title}".strip(": ")]
        if state or assignee:
            lines.append(f"Status: {state or 'unknown'} · Assignee: {assignee or 'unassigned'}")
        if team.get("name") or project.get("name"):
            lines.append(
                f"Team: {team.get('name', '-')} · Project: {project.get('name', '-')}"
            )
        if description:
            lines.append("")
            lines.append(description)
        content = "\n".join(lines).strip()

        tags = ["linear", "issue"]
        if team.get("key"):
            tags.append(f"team:{team['key']}")
        if state:
            tags.append(f"status:{state}")
        tags.extend(labels)

        return {
            "content": content[:8000],
            "tags": tags,
            "metadata": {
                "source": "linear",
                "type": "issue",
                "identifier": identifier,
                "url": issue.get("url", ""),
                "state": state,
                "priority": issue.get("priorityLabel", ""),
                "team": team.get("name", ""),
                "project": project.get("name", ""),
                "assignee": assignee,
                "labels": labels,
                "created_at": issue.get("createdAt", ""),
                "updated_at": issue.get("updatedAt", ""),
            },
        }

    async def _fetch_comments(self, client: Any, identifier: str) -> str:
        """Return an issue's comments as a text block, or an empty string."""
        import httpx

        if not identifier:
            return ""
        try:
            resp = await client.post(
                LINEAR_API,
                headers=self._headers,
                json={"query": _COMMENTS_QUERY, "variables": {"issueId": identifier, "first": 20}},
            )
        except httpx.HTTPError:
            return ""
        if resp.status_code != 200:
            return ""

        payload = resp.json()
        if payload.get("errors"):
            return ""
        issue = (payload.get("data") or {}).get("issue") or {}
        nodes = ((issue.get("comments") or {}).get("nodes")) or []

        lines: list[str] = []
        for comment in nodes:
            author = (comment.get("user") or {}).get("displayName", "unknown")
            body = (comment.get("body") or "").strip()
            if body:
                lines.append(f"{author}: {body}")
        return "\n".join(lines)

    async def disconnect(self) -> None:
        """Nothing to release: every request uses a short-lived client."""
        self._headers = {}
