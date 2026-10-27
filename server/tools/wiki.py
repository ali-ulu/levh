"""MCP tools for the local source-linked LLM Wiki."""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from server.core.llm_wiki import LLMWiki, WikiError
from server.core.memory_engine import MemoryEngine
from server.core.tenancy import AuthorizationError


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def build_knowledge_wiki(llm_summaries: bool = False) -> str:
        """Build the local Markdown Wiki from LEVH's stored evidence.

        This writes credential-redacted raw snapshots, entity/project evidence
        pages, wiki/index.md, wiki/sources.md, wiki/log.md and manifest.json.
        LEVH's SQLite memory rows are not changed. If a memory was explicitly
        deleted, its mirrored Wiki snapshots are removed on the next build.
        LLM summaries are disabled by default. Set ANSWER_MODE=llm and
        OPENAI_API_KEY, then explicitly pass llm_summaries=true to request
        at most twelve cited summary drafts per build.

        Args:
            llm_summaries: Explicitly request cached, cited LLM draft summaries.
        """
        try:
            result = await LLMWiki(engine).build(llm_summaries=llm_summaries)
        except (WikiError, AuthorizationError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def lint_knowledge_wiki() -> str:
        """Check the local Wiki's source digests, evidence links, page hashes and manifest.

        Read-only: does not repair or rewrite files. Run this after
        build_knowledge_wiki; each issue has a code, path and detail.
        """
        try:
            result = LLMWiki(engine).lint()
        except (WikiError, AuthorizationError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def search_knowledge_wiki(query: str, limit: int = 10) -> str:
        """Search the generated Markdown Wiki and return page IDs, snippets and paths.

        Search is local literal matching over generated pages. Use the returned
        page ID with get_knowledge_wiki_page to read the complete page.
        """
        try:
            result = LLMWiki(engine).search(query, limit=limit)
        except (WikiError, AuthorizationError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def get_knowledge_wiki_page(page_id: str) -> str:
        """Read one manifest-indexed Wiki page by its page ID.

        The returned Markdown includes numbered evidence citations and links
        to the credential-redacted raw snapshot files.
        """
        try:
            result = LLMWiki(engine).get_page(page_id)
        except (WikiError, AuthorizationError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        if result is None:
            return json.dumps({"ok": False, "error": "page_not_found"}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False, indent=2)
