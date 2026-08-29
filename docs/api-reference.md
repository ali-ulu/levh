# REST API Reference

Every endpoint the server exposes. Generated from the running app and locked
by `tests/test_docs_match_code.py`, so a new route cannot land here undocumented.

All `/api/*` endpoints except `/api/health` require `X-LEVH-Token` when
`LEVH_TOKEN` is set. Under `LEVH_PUBLIC_DEMO=true` every mutating method is
refused, as are the bulk exports; `POST /api/memories/recall` stays open
because it is a read that has to POST to carry its query.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memories` | List with filters (`q`, `project`, `source`, `tag`, `pinned`, `memory_type`, `limit`, `offset`) |
| POST | `/api/memories` | Default product write path: admission gate before persistence |
| POST | `/api/memories/admit` | Store a candidate memory through the admission gate: dedupe + secret redaction. reject/review are not stored unless… |
| GET | `/api/memories/audit-secrets` | Read-only scan for secrets (credentials, tokens) that slipped into stored memories before the admission gate existed |
| POST | `/api/memories/consolidate` | Promote short-term memories to episodic |
| POST | `/api/memories/consolidate-similar` | Preview (dry_run) or apply sleep-like consolidation: cluster related older memories and compress each cluster into… |
| POST | `/api/memories/dedupe` | Find (dry_run) or remove near-duplicate memories |
| POST | `/api/memories/evaluate-admission` | Preview the admission gate's verdict for a candidate memory WITHOUT storing it: admit / review / redact / reject |
| POST | `/api/memories/export` | Export every memory as JSON |
| GET | `/api/memories/fading` | Memories predicted to be nearly forgotten — the review queue |
| GET | `/api/memories/held` | Candidates the admission gate answered `review` for and parked for a human. Not memories yet — distinct from `/api/memories/review`. `status=""` lists every decision state |
| POST | `/api/memories/held/{held_id}/admit` | Keep a held candidate: store it as the memory it was going to be, with the importance, tags, session, project, source and type it arrived with |
| POST | `/api/memories/held/{held_id}/discard` | Drop a held candidate. The row stays with its verdict, so the discard is recorded |
| POST | `/api/memories/import` | Import memories from JSON |
| GET | `/api/memories/low-trust` | Stored memories whose provenance/trust confidence is below ``threshold`` (least confident first). Run… |
| POST | `/api/memories/recall` | Recall by query, ranked by H(x,ψ). Reinforcement is forced off in public demo mode |
| POST | `/api/memories/redact-all` | Bulk redaction of secrets across stored memories. dry_run=true (default) only previews; set false to rewrite every… |
| GET | `/api/memories/review` | Spaced-repetition review queue — fading, unpinned, un-snoozed memories due for a… |
| POST | `/api/memories/trust/recompute` | Compute and persist the provenance/trust score for every memory |
| GET | `/api/memories/{memory_id}` | Get one memory |
| DELETE | `/api/memories/{memory_id}` | Delete a memory from all three layers |
| PUT | `/api/memories/{memory_id}` | Update content, importance, tags or project — routed through the admission gate |
| POST | `/api/memories/{memory_id}/feedback` | Learn from recall outcomes: helpful=true reinforces the memory, helpful=false weakens it so wrong/stale information… |
| GET | `/api/memories/{memory_id}/forgetting-curve` | Predicted retention curve for a memory — powers the 'memory strength' visualization in the dashboard's detail drawer |
| PATCH | `/api/memories/{memory_id}/pin` | Pin or unpin. Pinned memories never decay and always reach context files |
| POST | `/api/memories/{memory_id}/purge` | Hard-delete a memory across every layer and verify nothing survives. Pinned memories are purged too — this is a… |
| POST | `/api/memories/{memory_id}/redact` | Strip secrets from an already-stored memory in place, recorded auditably in its metadata's redaction_history |
| POST | `/api/memories/{memory_id}/reinforce` | Manually strengthen a memory — resets its decay clock and grows its stability, the same reinforcement that happens… |
| GET | `/api/memories/{memory_id}/related` | Memories most similar to this one — the 'related memories' graph edge, computed live from embeddings. Powers 'see… |
| POST | `/api/memories/{memory_id}/review` | Apply a spaced-repetition review decision to a memory |
| GET | `/api/memories/{memory_id}/score-breakdown` | Return H(x,ψ) score breakdown for a specific memory + query pair |
| GET | `/api/memories/{memory_id}/trust` | Provenance/trust breakdown for a memory — explainable, deterministic, NOT truth, and independent of H-score recall… |
| GET | `/api/sessions` | List sessions |
| POST | `/api/sessions` | Create a named session |
| GET | `/api/sessions/{session_id}` | Get one session |
| PATCH | `/api/sessions/{session_id}/end` | End a session and consolidate its memories |
| POST | `/api/sessions/{session_id}/summarize` | Distill a session's memories into one durable summary memory (LLM when OPENAI_API_KEY is set, deterministic… |
| GET | `/api/projects` | Projects with memory counts |
| GET | `/api/sources` | Which clients stored memories, with counts |
| GET | `/api/tags` | Tags with counts |
| GET | `/api/people` | Distinct people across all memories (calendar attendees, email senders/recipients, transcript speakers),… |
| GET | `/api/people/{key}` | A person's profile plus every memory that mentions them. ``key`` may be an email, a person key, or a free-text name… |
| GET | `/api/organizations` | Distinct organizations across all memories (people grouped by email domain), most-frequent first |
| GET | `/api/organizations/{key}` | An organization's profile plus every memory that mentions someone from it. ``key`` may be a domain or a free-text… |
| GET | `/api/timeline` | Episodic memories grouped by day, most recent first — "what happened this/last week" |
| GET | `/api/briefing` | Deterministic Daily Briefing — what's on today, open commitments from recent memories, and memories that are fading… |
| GET | `/api/meeting-prep` | Proactive pre-meeting brief — the next upcoming meeting (or a matched one), each attendee's recent context, and… |
| GET | `/api/decisions` | Deterministic decision detection — statements like "we decided" / "agreed to" / "karar verdik" in recent episodic… |
| GET | `/api/context` | The current context window — short-term, pinned and important memories |
| POST | `/api/ask` | Ask your memory a question and get a synthesized, cited answer |
| POST | `/api/attachments/upload` | Store an uploaded file locally and return the path to attach from |
| POST | `/api/memories/{memory_id}/attachments` | Attach a local file to a memory by reference (path + sha256), with optional derived text (OCR/transcript/caption)… |
| GET | `/api/memories/{memory_id}/attachments` | List the files attached to a memory |
| POST | `/api/attachments/{attachment_id}/verify` | Re-check the file against what was recorded at attach time. A missing or changed file raises a conflict candidate… |
| POST | `/api/attachments/verify-all` | Verify every attachment. Returns counts by resulting status |
| DELETE | `/api/attachments/{attachment_id}` | Delete an attachment record (the referenced file on disk is left untouched) |
| GET | `/api/entities` | Persisted entities (optionally filtered by type), most-mentioned first |
| POST | `/api/entities/reindex` | Rebuild the persistent entity graph from every stored memory |
| GET | `/api/entities/stats` | Counts of persisted entities by type |
| GET | `/api/entities/{entity_id}` | An entity's profile: the memories that mention it and the entities it co-occurs with. ``entity_id`` may be a full id… |
| POST | `/api/guard/mistakes` | Record a mistake as a pinned rule plus a violation row |
| GET | `/api/guard/rules` | List the pinned rules mistakes have produced, most important first |
| GET | `/api/guard/violations` | List recorded mistakes, newest first. ``days=0`` means all time |
| GET | `/api/conflicts` | List conflict candidates, optionally filtered by status. Pass an empty status to list every status |
| POST | `/api/conflicts/detect` | Scan stored memories for conflict CANDIDATES — pairs that share an entity and show an opposing surface pattern.… |
| POST | `/api/conflicts/{conflict_id}/review` | Apply a human review decision to a conflict candidate. ``conflict_id`` may contain ``|`` so the path converter is used |
| GET | `/api/connectors` | List available connectors and their status |
| POST | `/api/connectors/import` | Import data from an external app via connector |
| POST | `/api/connectors/sync` | Connector v2 ingest: fetch, then route items through the admission gate (dedupe + secret redaction), with… |
| GET | `/api/connectors/sync-state` | Per-source sync bookkeeping: last synced, totals, run count |
| POST | `/api/connectors/upload` | Store an uploaded file locally and return the path to import from |
| GET | `/api/connectors/{name}/config` | Get required config fields for a connector |
| GET | `/api/export/full.json` | One-shot audit bundle: memories, entity graph, trust scores, and conflict candidates — the raw machine-readable record |
| GET | `/api/export/full.pdf` | Human-readable audit report (summary counts, entity/trust/conflict overview) rendered from the same data as the JSON… |
| GET | `/api/export/full.sqlite` | Raw SQLite copy of the live database, taken via the online backup API |
| POST | `/api/backup` | Full portable snapshot (all memories + sessions) as a downloadable file. When ``passphrase`` is set the file is… |
| POST | `/api/restore` | Restore from a backup file. ``content_b64`` is the base64-encoded backup bytes (encrypted or plain — auto-detected).… |
| POST | `/api/import/file` | Turn an arbitrary uploaded file into memories. Plain text, PDF, Word, Excel and zip archives are extracted to text… |
| POST | `/api/onboarding/mcp-config` | Generate a focused MCP client config without persisting secrets |
| POST | `/api/onboarding/remove-demo` | Remove only metadata.demo=true memories using the audited purge path |
| GET | `/api/onboarding/status` | Real first-run readiness derived from local storage/configuration |
| POST | `/api/seed-demo` | Populate an empty store with a deterministic demo corpus (onboarding). Refuses to run on a non-empty store unless… |
| GET | `/api/stats` | System statistics and metrics |
| GET | `/api/config` | Current server configuration (for the Settings page) |
| GET | `/api/health` | Health |
| POST | `/api/benchmark/recall` | Run the recall-quality benchmark harness (hit@k / MRR on a labelled corpus) and return the metrics — powers the… |
| POST | `/api/context-file` | Generate a CLAUDE.md / .cursorrules style context file from memories |
| WS | `/ws/memory` | Real-time event stream + RPC actions (recall/stats/ping; writes blocked in public demo mode) |
| SSE | `/api/mcp/sse` | MCP SSE stream endpoint |
