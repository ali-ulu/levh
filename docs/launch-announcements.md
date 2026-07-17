# LEVH launch announcements

These are copy-ready launch drafts. Adapt the first sentence to the audience;
keep the install command and links intact.

## GitHub Discussion

### LEVH is live: local-first memory for AI agents and humans

LEVH is now publicly available.

It gives Claude, Cursor, VS Code and other MCP-compatible tools persistent,
searchable memory across sessions and projects. The system is local-first:
SQLite storage, deterministic offline fallback, a built-in dashboard, and no
hosted account required.

What is included:

- 59 MCP tools for capture, recall, review, trust, entities, conflicts and
  project context.
- Memory decay and reinforcement so important context stays available while
  stale detail fades.
- A local dashboard plus REST API and WebSocket activity feed.

Install it with:

```bash
pip install levh
levh doctor
```

- Website: https://ali-ulu.github.io/levh/
- PyPI: https://pypi.org/project/levh/
- Source: https://github.com/ali-ulu/levh

LEVH is licensed under AGPL-3.0-or-later. Feedback, bug reports and real
workflow examples are welcome.

## Show HN

### Title

Show HN: LEVH – local-first memory for Claude, Cursor and MCP coding tools

### Body

I built LEVH because coding agents keep restarting from zero context. It is a
local-first shared memory layer for MCP-compatible tools: store decisions and
project context once, then surface it in later sessions.

It uses SQLite, works without a hosted account, has deterministic offline
fallbacks, and ships with a dashboard, REST API, WebSocket feed and 59 MCP
tools. The memory model deliberately allows unused details to fade while
reinforced or pinned information stays durable.

Install: `pip install levh`

Demo/docs: https://ali-ulu.github.io/levh/

Source: https://github.com/ali-ulu/levh

I would especially value feedback from people using Claude Code, Cursor or
other MCP clients daily: what context do you repeatedly have to re-explain to
your coding agent?

## LinkedIn / X

LEVH is live: a local-first memory layer for AI agents and humans.

Give Claude, Cursor and other MCP clients persistent project context across
sessions — with SQLite, a dashboard, trust/entity signals and no hosted
account.

`pip install levh`

https://ali-ulu.github.io/levh/
https://github.com/ali-ulu/levh
