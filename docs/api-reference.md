# REST API Reference

Every endpoint the server exposes. Generated from the running app and locked
by `tests/test_docs_match_code.py`, so a new route cannot land here undocumented.

## Versions

`/api/v1/*` is the published contract. Its schema is frozen in the repository
root `openapi.json`, and `tests/test_openapi_contract.py` fails if a change to
the code alters it without the file being regenerated in the same commit - so
a breaking edit is a deliberate, reviewable diff rather than a silent one.

The older unversioned `/api/*` paths still serve the same handlers for
backward compatibility, but they are a compatibility alias only: they are
hidden from the OpenAPI schema and new clients should target `/api/v1`.
`/api/health` has no version - it is the liveness probe and must stay reachable
before the versioned surface is known to work.

All `/api/v1/*` endpoints except the probes require `X-LEVH-Token` when
`LEVH_TOKEN` is set. `/api/health` (liveness) and `/api/readyz` (readiness) are
both exempt: an orchestrator's probe cannot attach a header, and gating them
would make every check a 401. The generated docs surface the app serves at the
root (`/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect`) is part of
the same gate, so an authenticated deployment does not expose its route schema
anonymously. Under `LEVH_PUBLIC_DEMO=true` every mutating method is
refused, as are the bulk exports; `POST /api/memories/recall` stays open
because it is a read that has to POST to carry its query.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/memories` | List with filters (`q`, `project`, `source`, `tag`, `pinned`, `memory_type`, `limit`, `offset`) |
| POST | `/api/v1/memories` | Default product write path: admission gate before persistence |
| POST | `/api/v1/memories/admit` | Store a candidate memory through the admission gate: dedupe + secret redaction. reject/review are not stored unless… |
| GET | `/api/v1/memories/audit-secrets` | Read-only scan for secrets (credentials, tokens) that slipped into stored memories before the admission gate existed |
| POST | `/api/v1/memories/consolidate` | Promote short-term memories to episodic |
| POST | `/api/v1/memories/consolidate-similar` | Preview (dry_run) or apply sleep-like consolidation: cluster related older memories and compress each cluster into… |
| POST | `/api/v1/memories/dedupe` | Find (dry_run) or remove near-duplicate memories |
| POST | `/api/v1/memories/evaluate-admission` | Preview the admission gate's verdict for a candidate memory WITHOUT storing it: admit / review / redact / reject |
| POST | `/api/v1/memories/export` | Export every memory as JSON |
| GET | `/api/v1/memories/fading` | Memories predicted to be nearly forgotten — the review queue |
| GET | `/api/v1/memories/held` | Candidates the admission gate answered `review` for and parked for a human. Not memories yet — distinct from `/api/memories/review`. `status=""` lists every decision state |
| POST | `/api/v1/memories/held/{held_id}/admit` | Keep a held candidate: store it as the memory it was going to be, with the importance, tags, session, project, source and type it arrived with |
| POST | `/api/v1/memories/held/{held_id}/discard` | Drop a held candidate. The row stays with its verdict, so the discard is recorded |
| POST | `/api/v1/memories/import` | Import memories from JSON |
| GET | `/api/v1/memories/low-trust` | Stored memories whose provenance/trust confidence is below ``threshold`` (least confident first). Run… |
| POST | `/api/v1/memories/recall` | Recall by query, ranked by H(x,ψ). Reinforcement is forced off in public demo mode |
| POST | `/api/v1/memories/redact-all` | Bulk redaction of secrets across stored memories. dry_run=true (default) only previews; set false to rewrite every… |
| GET | `/api/v1/memories/review` | Spaced-repetition review queue — fading, unpinned, un-snoozed memories due for a… |
| POST | `/api/v1/memories/trust/recompute` | Compute and persist the provenance/trust score for every memory |
| GET | `/api/v1/memories/{memory_id}` | Get one memory |
| DELETE | `/api/v1/memories/{memory_id}` | Delete a memory from all three layers |
| PUT | `/api/v1/memories/{memory_id}` | Update content, importance, tags or project — routed through the admission gate |
| POST | `/api/v1/memories/{memory_id}/feedback` | Learn from recall outcomes: helpful=true reinforces the memory, helpful=false weakens it so wrong/stale information… |
| GET | `/api/v1/memories/{memory_id}/forgetting-curve` | Predicted retention curve for a memory — powers the 'memory strength' visualization in the dashboard's detail drawer |
| PATCH | `/api/v1/memories/{memory_id}/pin` | Pin or unpin. Pinned memories never decay and always reach context files |
| POST | `/api/v1/memories/{memory_id}/purge` | Hard-delete a memory across every layer and verify nothing survives. Pinned memories are purged too — this is a… |
| POST | `/api/v1/memories/{memory_id}/redact` | Strip secrets from an already-stored memory in place, recorded auditably in its metadata's redaction_history |
| POST | `/api/v1/memories/{memory_id}/reinforce` | Manually strengthen a memory — resets its decay clock and grows its stability, the same reinforcement that happens… |
| GET | `/api/v1/memories/{memory_id}/related` | Memories most similar to this one — the 'related memories' graph edge, computed live from embeddings. Powers 'see… |
| POST | `/api/v1/memories/{memory_id}/review` | Apply a spaced-repetition review decision to a memory |
| GET | `/api/v1/memories/{memory_id}/score-breakdown` | Return H(x,ψ) score breakdown for a specific memory + query pair |
| GET | `/api/v1/memories/{memory_id}/trust` | Provenance/trust breakdown for a memory — explainable, deterministic, NOT truth, and independent of H-score recall… |
| GET | `/api/v1/sessions` | List sessions |
| POST | `/api/v1/sessions` | Create a named session |
| GET | `/api/v1/sessions/{session_id}` | Get one session |
| DELETE | `/api/v1/sessions/{session_id}` | Delete a session. `memories=refuse` (default) deletes only an empty one and answers 409 with the count otherwise; `detach` keeps the memories and drops their session link; `delete` removes them too |
| PATCH | `/api/v1/sessions/{session_id}/end` | End a session and consolidate its memories |
| POST | `/api/v1/sessions/{session_id}/summarize` | Distill a session's memories into one durable summary memory (LLM when OPENAI_API_KEY is set, deterministic… |
| GET | `/api/v1/projects` | Projects with memory counts |
| GET | `/api/v1/sources` | Which clients stored memories, with counts |
| GET | `/api/v1/tags` | Tags with counts |
| GET | `/api/v1/people` | Distinct people across all memories (calendar attendees, email senders/recipients, transcript speakers),… |
| GET | `/api/v1/people/{key}` | A person's profile plus every memory that mentions them. ``key`` may be an email, a person key, or a free-text name… |
| GET | `/api/v1/organizations` | Distinct organizations across all memories (people grouped by email domain), most-frequent first |
| GET | `/api/v1/organizations/{key}` | An organization's profile plus every memory that mentions someone from it. ``key`` may be a domain or a free-text… |
| GET | `/api/v1/timeline` | Episodic memories grouped by day, most recent first — "what happened this/last week" |
| GET | `/api/v1/briefing` | Deterministic Daily Briefing — what's on today, open commitments from recent memories, and memories that are fading… |
| GET | `/api/v1/meeting-prep` | Proactive pre-meeting brief — the next upcoming meeting (or a matched one), each attendee's recent context, and… |
| GET | `/api/v1/decisions` | Deterministic decision detection — statements like "we decided" / "agreed to" / "karar verdik" in recent episodic… |
| GET | `/api/v1/context` | The current context window — short-term, pinned and important memories |
| POST | `/api/v1/ask` | Ask your memory a question and get a synthesized, cited answer |
| POST | `/api/v1/attachments/upload` | Store an uploaded file locally and return the path to attach from |
| POST | `/api/v1/memories/{memory_id}/attachments` | Attach a local file to a memory by reference (path + sha256), with optional derived text (OCR/transcript/caption)… |
| GET | `/api/v1/memories/{memory_id}/attachments` | List the files attached to a memory |
| POST | `/api/v1/attachments/{attachment_id}/verify` | Re-check the file against what was recorded at attach time. A missing or changed file raises a conflict candidate… |
| POST | `/api/v1/attachments/verify-all` | Verify every attachment. Returns counts by resulting status |
| DELETE | `/api/v1/attachments/{attachment_id}` | Delete an attachment record (the referenced file on disk is left untouched) |
| GET | `/api/v1/entities` | Persisted entities (optionally filtered by type), most-mentioned first |
| POST | `/api/v1/entities/reindex` | Rebuild the persistent entity graph from every stored memory |
| GET | `/api/v1/entities/stats` | Counts of persisted entities by type |
| GET | `/api/v1/entities/{entity_id}` | An entity's profile: the memories that mention it and the entities it co-occurs with. ``entity_id`` may be a full id… |
| POST | `/api/v1/guard/mistakes` | Record a mistake as a pinned rule plus a violation row |
| GET | `/api/v1/guard/rules` | List the pinned rules mistakes have produced, most important first |
| GET | `/api/v1/guard/violations` | List recorded mistakes, newest first. ``days=0`` means all time |
| GET | `/api/v1/conflicts` | List conflict candidates, optionally filtered by status. Pass an empty status to list every status |
| POST | `/api/v1/conflicts/detect` | Scan stored memories for conflict CANDIDATES — pairs that share an entity and show an opposing surface pattern.… |
| POST | `/api/v1/conflicts/{conflict_id}/review` | Apply a human review decision to a conflict candidate. ``conflict_id`` may contain ``|`` so the path converter is used |
| GET | `/api/v1/findings` | The findings inbox: what LEVH noticed about itself, newest sighting first. Pass an empty status to list every state |
| POST | `/api/v1/findings` | Record a finding. Scrubbed (home paths, username, secrets) and fingerprinted before storage, so a repeat folds into the existing row instead of adding one |
| POST | `/api/v1/findings/{finding_id}/decide` | Apply a human decision: `ack`, `resolved`, `ignored`, or `open` to reopen. The only way a finding changes state |
| DELETE | `/api/v1/findings/{finding_id}` | Delete a finding outright |
| GET | `/api/v1/librarian/status` | The watcher's current scan: which agents are connected to levh, and 24h memory activity. Read-only |
| POST | `/api/v1/librarian/scan` | Run one scan now and write any findings to the inbox |
| POST | `/api/v1/librarian/chat` | Ask the watcher a question. **Can execute shell commands and edit agent config files when the model proposes an action** — set `LEVH_LIBRARIAN=0` to disable the watcher, and see the security note below |
| GET | `/librarian` | Standalone watcher chat page (HTML) |
| GET | `/librarian.js` | Chat widget script injected into dashboard pages |
| GET | `/api/v1/connectors` | List available connectors and their status |
| POST | `/api/v1/connectors/import` | Import data from an external app via connector |
| POST | `/api/v1/connectors/sync` | Connector v2 ingest: fetch, then route items through the admission gate (dedupe + secret redaction), with… |
| GET | `/api/v1/connectors/sync-state` | Per-source sync bookkeeping: last synced, totals, run count |
| POST | `/api/v1/connectors/upload` | Store an uploaded file locally and return the path to import from |
| GET | `/api/v1/connectors/{name}/config` | Get required config fields for a connector |
| GET | `/api/v1/export/full.json` | One-shot audit bundle: memories, entity graph, trust scores, and conflict candidates — the raw machine-readable record |
| GET | `/api/v1/export/full.pdf` | Human-readable audit report (summary counts, entity/trust/conflict overview) rendered from the same data as the JSON… |
| GET | `/api/v1/export/full.sqlite` | Raw SQLite copy of the live database, taken via the online backup API |
| POST | `/api/v1/backup` | Full portable snapshot (all memories + sessions) as a downloadable file. When ``passphrase`` is set the file is… |
| POST | `/api/v1/restore` | Restore from a backup file. ``content_b64`` is the base64-encoded backup bytes (encrypted or plain — auto-detected).… |
| POST | `/api/v1/import/file` | Turn an arbitrary uploaded file into memories. Plain text, PDF, Word, Excel and zip archives are extracted to text… |
| POST | `/api/v1/onboarding/mcp-config` | Generate a focused MCP client config without persisting secrets |
| POST | `/api/v1/onboarding/remove-demo` | Remove only metadata.demo=true memories using the audited purge path |
| GET | `/api/v1/onboarding/status` | Real first-run readiness derived from local storage/configuration |
| POST | `/api/v1/seed-demo` | Populate an empty store with a deterministic demo corpus (onboarding). Refuses to run on a non-empty store unless… |
| GET | `/api/v1/stats` | System statistics and metrics |
| GET | `/api/v1/config` | Current server configuration (for the Settings page) |
| GET | `/api/health` | Health |
| GET | `/api/v1/readyz` | Readiness probe: pings SQLite, reports the live embedder mode and whether derived state is behind. 503 with reasons when not ready |
| GET | `/api/v1/metrics` | In-process Prometheus metrics: recall/store latency histograms, DB lock-wait, embedder fallback, admission verdicts, derived-rebuild outcomes |
| POST | `/api/v1/benchmark/recall` | Run the recall-quality benchmark harness (hit@k / MRR on a labelled corpus) and return the metrics — powers the… |
| POST | `/api/v1/context-file` | Generate a CLAUDE.md / .cursorrules style context file from memories |
| POST | `/api/v1/agents/connect` | Record an agent connecting to LEVH. Returns agent session ID and presence info |
| POST | `/api/v1/agents/{agent_session_id}/heartbeat` | Send a heartbeat to keep an agent connection alive |
| POST | `/api/v1/agents/{agent_session_id}/disconnect` | Disconnect an agent from LEVH |
| GET | `/api/v1/agents` | List all agent connections (active and disconnected) |
| GET | `/api/v1/agents/online` | List currently online agents |
| GET | `/api/v1/agents/stats` | Aggregate agent usage statistics |
| POST | `/api/v1/checkpoints` | Create a checkpoint of current work state |
| GET | `/api/v1/checkpoints` | List recent checkpoints, optionally filtered by agent or project |
| GET | `/api/v1/agents/{agent_name}/metrics` | Get performance metrics for a specific agent |
| GET | `/api/v1/agents/metrics/usage` | Get usage billing metrics for all agents |
| GET | `/api/v1/agents/collaboration/{project}` | Get collaboration info for agents on the same project |
| WS | `/ws/agents` | WebSocket for real-time agent presence updates |
| WS | `/ws/memory` | Real-time event stream + RPC actions (recall/stats/ping; writes blocked in public demo mode) |
| SSE | `/api/mcp/sse` | MCP SSE stream endpoint |
