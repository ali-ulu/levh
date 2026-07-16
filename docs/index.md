---
title: LEVH
description: Shared memory for AI coding tools
---

# LEVH

Shared, local-first memory for AI coding tools.

LEVH gives Claude, Cursor, VS Code and other MCP-compatible tools persistent,
searchable memory across sessions and projects. It runs locally with SQLite,
ships with a dashboard, and exposes 59 MCP tools plus a REST API.

## Install

```bash
pip install levh
levh doctor
levh setup
```

## Links

- [Source code on GitHub](https://github.com/ali-ulu/levh)
- [LEVH on PyPI](https://pypi.org/project/levh/)
- [Quick start](https://github.com/ali-ulu/levh#quick-start)
- [MCP client configuration](mcp-client-config.md)
- [Architecture](ARCHITECTURE.md)

## Why LEVH?

- Local-first SQLite storage; no hosted account or external database required.
- Memory decay, reinforcement, trust, conflict and entity graph signals.
- MCP server, REST API, WebSocket activity feed and static dashboard.
- Deterministic offline fallback when no model API is configured.

## License

LEVH is released under the [GNU Affero General Public License v3.0 or later](https://www.gnu.org/licenses/agpl-3.0.html).
