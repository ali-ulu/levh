"""Store one memory over the real MCP SSE transport.

Invoked by e2e/mcp.spec.ts as a subprocess:

    uv run --frozen python e2e/mcp_store.py <sse-url> <content>

Speaks the actual MCP protocol through the official client, so the spec
exercises the same handshake, tool listing and tool call an external AI client
performs — not a shortcut into the engine.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.sse import sse_client


async def main() -> int:
    url, content = sys.argv[1], sys.argv[2]
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "store_memory", {"content": content, "source": "e2e-mcp"}
            )
            text = "\n".join(getattr(item, "text", "") for item in result.content)
            print(text)
            return 0 if "stored successfully" in text else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
