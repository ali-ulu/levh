# Security Policy

StackMemory is a local-first developer tool. Treat memory databases as sensitive because they may contain project decisions, customer context, private notes, or secrets accidentally pasted by a user.

## Supported versions

This repository is in alpha/beta hardening. Security fixes target the current `main` branch first.

## Reporting a vulnerability

Open a private security advisory or contact the repository owner. Do not publish exploit details before maintainers have had time to respond.

## Local deployment guidance

- Keep StackMemory bound to localhost or a trusted private network.
- Set `STACKMEMORY_TOKEN` before exposing `/api/*` beyond your own machine.
- Keep `STACKMEMORY_CORS_ORIGINS` restricted to trusted origins.
- Do not commit `.env`, `stackmemory.db`, exported memories, logs, or generated runtime artifacts.
- Use `EMBEDDER_MODE=hash` only for tests/smoke demos; use `local`, `ollama`, or `openai` for real semantic quality.
