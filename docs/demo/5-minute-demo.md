# StackMemory in 5 minutes

A guided, fully-offline walkthrough. No API key, no LLM, no network: the demo
uses the deterministic hash embedder and a local SQLite database.

## 0. Install

```bash
pip install stackmemory
stackmemory doctor
```

A zero-memory database is a first-run warning, not an installation failure.

## 1. Choose the demo path

Run the idempotent setup command:

```bash
stackmemory setup --demo --client claude --profile work
```

This performs the real product path:

- initializes local storage without resetting an existing database;
- loads 20 explicitly marked demo memories only when the store is empty;
- builds entities, trust breakdowns, and one deterministic conflict candidate;
- generates a Claude MCP configuration using the `work` profile;
- writes a privacy-safe local onboarding receipt.

Expected demo state:

```text
20 memories
21 entities
52 memory/entity links
1 conflict candidate
```

`setup --demo` and `seed-demo` refuse to overwrite a non-empty store. Demo
memories carry `metadata.demo=true` and display a **Demo data** badge.

## 2. Open the dashboard

```bash
stackmemory serve
```

Open <http://localhost:8000>. The first-run card shows real backend readiness,
not a fake completion flag. Walk these surfaces:

1. **Briefing**: local digest of commitments, fading memories, and work context.
2. **People / Organizations / Graph**: 4 people, 2 organizations, and their
   co-occurrence relationships.
3. **Trust breakdown**: source, corroboration, review, recency, and risk. This is
   provenance/confidence, not a truth verdict.
4. **Conflicts**: one Atlas deadline candidate (`2026-03-15` vs `2026-04-02`).
   StackMemory asks for review; it does not auto-resolve or delete.
5. **Insights**: deterministic forgetting and reinforcement behavior.

## 3. Connect an AI client

The setup command prints the generated configuration. The dashboard can also
generate and copy a configuration for a supported client.

MCP profiles reduce the advertised tool surface; they are **not** an
authorization or security boundary.

```bash
stackmemory mcp profiles
stackmemory mcp config claude --profile work
```

Profile counts are read from the live registry:

```text
minimal    5 tools
work      15 tools
admin     54 tools
full      59 tools
```

Once connected, ask:

```text
What did we decide about the Atlas datastore?
```

The client should call `recall_memory` and return the seeded PostgreSQL decision
with its provenance.

## 4. Start with real data instead

The real-data path does not seed anything:

```bash
stackmemory setup --real --client claude --profile work
stackmemory capture "Atlas uses PostgreSQL in production."
stackmemory serve
```

The first memory passes through the ordinary admission and storage pipeline. It
is not silently pinned and its trust score is not artificially increased.

## 5. Local dogfood metrics

Usage measurement is local and disabled by default. When explicitly enabled,
StackMemory records only whitelisted events in a local JSONL journal. Raw memory
and query content are not recorded, and nothing is sent over the network.

```bash
STACKMEMORY_DOGFOOD_ENABLED=true stackmemory serve
```

A process started without the flag must be restarted to enable collection. Old
journal files are not migrated automatically, and enabling the flag does not
create historical events.

## 6. Remove demo data safely

Use **Remove demo data** in the first-run card and confirm the action. The cleanup
uses the audited purge path and deletes only memories carrying the demo marker.
Real memories in the same database survive.

The API equivalent is:

```bash
curl -X POST http://127.0.0.1:8000/api/onboarding/remove-demo \
  -H 'Content-Type: application/json' \
  -d '{"confirm": true}'
```

This is not a database reset and is intentionally not exposed as an MCP tool.
