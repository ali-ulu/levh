"""Notion Connector — Pull pages and databases from Notion via API.

Uses the Notion API directly with httpx (no SDK dependency).

Config keys:
    api_key (str): Notion integration token (starts with ``ntn_`` or ``secret_``).
    database_ids (list[str], optional): Specific database IDs to fetch.
    page_ids (list[str], optional): Specific page IDs to fetch.
    max_pages (int, optional): Max pages to fetch per database. Default 100.
"""

from __future__ import annotations

import os
from typing import Any

from .base import BaseConnector

# Notion API base
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionConnector(BaseConnector):
    """Import pages and databases from Notion."""

    name: str = "notion"
    description: str = (
        "Import pages and databases from Notion via API. "
        "Requires a Notion integration token (NOTION_API_KEY)."
    )

    def __init__(self) -> None:
        self._api_key: str = ""
        self._headers: dict[str, str] = {}
        self._database_ids: list[str] = []
        self._page_ids: list[str] = []
        self._max_pages: int = 100

    def required_config_keys(self) -> list[str]:
        return ["api_key"]

    async def connect(self, config: dict) -> bool:
        """Validate the Notion API key.

        Config keys:
            api_key (str): Notion integration token.
            database_ids (list[str], optional): Database IDs to fetch.
            page_ids (list[str], optional): Page IDs to fetch.
            max_pages (int, optional): Max pages per database. Default 100.
        """
        api_key = config.get("api_key", "") or os.getenv("NOTION_API_KEY", "")
        if not api_key:
            raise ValueError(
                "Notion API key is required. "
                "Pass it via config['api_key'] or set NOTION_API_KEY env var."
            )

        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        self._database_ids = config.get("database_ids", [])
        self._page_ids = config.get("page_ids", [])
        self._max_pages = config.get("max_pages", 100)

        # Quick validation call
        import httpx
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            resp = await client.get(
                f"{NOTION_API}/users/me",
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code != 200:
                raise ConnectionError(
                    f"Notion API returned {resp.status_code}: {resp.text[:200]}"
                )

        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Fetch pages from Notion and return as memory dicts."""
        import httpx

        memories: list[dict] = []

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0, pool=10.0)
        ) as client:
            # Fetch specific pages
            for page_id in self._page_ids:
                page_data = await self._fetch_page(client, page_id)
                if page_data:
                    memories.append(page_data)

            # Fetch from databases
            database_ids = self._database_ids
            if not database_ids:
                database_ids = await self._discover_databases(client)

            for db_id in database_ids:
                db_memories = await self._fetch_database(client, db_id)
                memories.extend(db_memories)

        return memories

    async def disconnect(self) -> None:
        self._api_key = ""
        self._headers = {}

    # ── Internal helpers ───────────────────────────────────────────

    async def _discover_databases(self, client: Any) -> list[str]:
        """Search for accessible databases in the integration's workspace."""
        databases: list[str] = []
        cursor: str | None = None

        for _ in range(5):  # max 5 pages of results
            body: dict[str, Any] = {
                "filter": {"value": "database", "property": "object"},
                "page_size": 100,
            }
            if cursor:
                body["start_cursor"] = cursor

            resp = await client.post(
                f"{NOTION_API}/search",
                headers=self._headers,
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for result in data.get("results", []):
                databases.append(result["id"])

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return databases

    async def _fetch_database(self, client: Any, db_id: str) -> list[dict]:
        """Fetch all pages from a Notion database."""
        import httpx

        memories: list[dict] = []
        cursor: str | None = None
        count = 0

        # Get database title
        db_title = db_id
        try:
            resp = await client.get(
                f"{NOTION_API}/databases/{db_id}",
                headers=self._headers,
                timeout=15,
            )
            if resp.status_code == 200:
                db_info = resp.json()
                title_parts = db_info.get("title", [])
                db_title = " ".join(t.get("plain_text", "") for t in title_parts) or db_id
        except httpx.HTTPError:
            pass

        while count < self._max_pages:
            body: dict[str, Any] = {"page_size": min(100, self._max_pages - count)}
            if cursor:
                body["start_cursor"] = cursor

            resp = await client.post(
                f"{NOTION_API}/databases/{db_id}/query",
                headers=self._headers,
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for page in data.get("results", []):
                page_mem = await self._page_to_memory(client, page, db_title)
                if page_mem:
                    memories.append(page_mem)
                    count += 1

            if not data.get("has_more") or count >= self._max_pages:
                break
            cursor = data.get("next_cursor")

        return memories

    async def _fetch_page(self, client: Any, page_id: str) -> dict | None:
        """Fetch a single Notion page with its content blocks."""
        import httpx

        resp = await client.get(
            f"{NOTION_API}/pages/{page_id}",
            headers=self._headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        page = resp.json()
        return await self._page_to_memory(client, page)

    async def _page_to_memory(
        self, client: Any, page: dict, source_db: str = ""
    ) -> dict | None:
        """Convert a Notion page object to a memory-compatible dict."""
        import httpx

        page_id = page["id"]
        title = self._extract_page_title(page)

        # Fetch content blocks
        blocks_text_parts: list[str] = []
        cursor: str | None = None

        for _ in range(10):  # max 10 block pages
            url = f"{NOTION_API}/blocks/{page_id}/children"
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor

            try:
                resp = await client.get(
                    url, headers=self._headers, params=params, timeout=15
                )
                if resp.status_code != 200:
                    break
                block_data = resp.json()
            except httpx.HTTPError:
                break

            for block in block_data.get("results", []):
                text = self._block_to_text(block)
                if text:
                    blocks_text_parts.append(text)

            if not block_data.get("has_more"):
                break
            cursor = block_data.get("next_cursor")

        content = f"{title}\n\n" + "\n".join(blocks_text_parts)
        content = content.strip()
        if not content:
            return None

        # Extract properties as tags
        tags = ["notion"]
        if source_db:
            tags.append(f"db:{source_db}")

        properties = page.get("properties", {})
        for prop_key, prop_val in properties.items():
            prop_type = prop_val.get("type", "")
            if prop_type == "select" and prop_val.get("select"):
                tags.append(prop_val["select"].get("name", ""))
            elif prop_type == "multi_select":
                for sel in prop_val.get("multi_select", []):
                    tags.append(sel.get("name", ""))
            elif prop_type == "status" and prop_val.get("status"):
                tags.append(prop_val["status"].get("name", ""))

        # Add tags from properties metadata
        if "tags" in properties:
            tags_prop = properties["tags"]
            if tags_prop.get("type") == "multi_select":
                for sel in tags_prop.get("multi_select", []):
                    tag_name = sel.get("name", "")
                    if tag_name and tag_name not in tags:
                        tags.append(tag_name)

        metadata: dict[str, Any] = {
            "source": "notion",
            "page_id": page_id,
            "title": title,
            "database": source_db,
            "url": page.get("url", ""),
            "created_time": page.get("created_time", ""),
            "last_edited_time": page.get("last_edited_time", ""),
        }

        return {
            "content": content,
            "tags": tags,
            "metadata": metadata,
        }

    @staticmethod
    def _extract_page_title(page: dict) -> str:
        """Extract the title text from a Notion page's properties."""
        properties = page.get("properties", {})
        for prop_key in ("title", "Name", "name", "Title"):
            if prop_key in properties:
                prop = properties[prop_key]
                if prop.get("type") == "title":
                    title_parts = prop.get("title", [])
                    return " ".join(t.get("plain_text", "") for t in title_parts).strip()
        return "Untitled"

    @staticmethod
    def _block_to_text(block: dict) -> str:
        """Convert a single Notion block to plain text."""
        block_type = block.get("type", "")

        if block_type in ("paragraph", "heading_1", "heading_2", "heading_3",
                          "bulleted_list_item", "numbered_list_item",
                          "quote", "callout", "toggle"):
            rich_text = block.get(block_type, {}).get("rich_text", [])
            return "".join(rt.get("plain_text", "") for rt in rich_text)

        if block_type == "code":
            rich_text = block.get("code", {}).get("rich_text", [])
            lang = block.get("code", {}).get("language", "")
            code = "".join(rt.get("plain_text", "") for rt in rich_text)
            return f"```{lang}\n{code}\n```"

        if block_type == "divider":
            return "---"

        if block_type == "table":
            # Simplified table rendering
            rows = block.get("table", {}).get("table_width", 0)
            return f"[Table: {rows} columns]"

        if block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            return f"[Bookmark: {url}]"

        if block_type == "image":
            caption_parts = block.get("image", {}).get("caption", [])
            caption = "".join(rt.get("plain_text", "") for rt in caption_parts)
            return f"[Image: {caption}]" if caption else "[Image]"

        return ""