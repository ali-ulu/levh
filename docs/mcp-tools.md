# MCP Tools

The complete MCP tool surface. See the [README](../README.md) for an overview.

> **Tool profiles.** Advertising all 73 tools to a client hurts tool-selection
> accuracy, so LEVH groups them into cumulative profiles —
> `minimal` (6) ⊂ `work` (28) ⊂ `admin` (68) ⊂ `full` (73). Generated configs
> default to **`work`**; run `levh mcp profiles` to see the bands, or set
> `LEVH_MCP_PROFILE` / `mcp config --profile <name>` to change it. The
> full table below is the `full` surface. Profiles only filter which tools a
> client sees — they are **not** an authentication or authorization boundary;
> every profile talks to the same engine instance with the same access.

| # | Tool | Description |
|---|------|-------------|
| 1 | `store_memory` | Store a memory with importance, tags, project, source, pin |
| 2 | `recall_memory` | Recall memories ranked by H(x,ψ) score (filter by session/project) |
| 3 | `search_memory` | Semantic search with detailed results |
| 4 | `get_context` | Build context window (short-term + pinned + important) |
| 5 | `get_memory_stats` | System statistics and metrics |
| 6 | `get_continuity_brief` | Get continuity brief — call at session start to load context from previous work |
| 7 | `list_memories` | List with type/tag/session/project/source/pinned filters |
| 8 | `ask_memory` | Ask your memory a question → synthesized, cited answer (read-only) |
| 9 | `reinforce_memory` | Manually strengthen a memory — resets decay clock, grows stability |
| 10 | `pin_memory` | Pin a memory — exempt from decay, always in context files |
| 11 | `attach_file` | Attach a local file (screenshot, PDF, recording, ...) to a memory as evidence — reference + derived text, not blob |
| 12 | `briefing` | Daily briefing — today's events, open commitments, and what you may be forgetting |
| 13 | `meeting_prep` | Pre-meeting brief — next meeting, attendees, what you last discussed, open items |
| 14 | `list_entities` | List graph entities by type, most-mentioned first |
| 15 | `about_entity` | One entity's profile — its memories and co-occurring entities |
| 16 | `memory_trust` | A memory's provenance/trust breakdown (confidence + explainable evidence) |
| 17 | `list_conflict_candidates` | List conflict candidates by status (open/confirmed/…) |
| 18 | `record_mistake` | Record a corrected mistake as a pinned rule — never decays, leads generated context files |
| 19 | `unpin_memory` | Restore normal decay |
| 20 | `update_memory` | Update content/importance/tags |
| 21 | `set_importance` | Set importance (0.0-1.0) |
| 22 | `forget_memory` | Delete from all 3 layers |
| 23 | `memory_feedback` | helpful=true reinforces; helpful=false makes wrong/stale info fade fast |
| 24 | `related_memories` | Nearest-neighbour "see also" for a memory (live, embedding-based) |
| 25 | `timeline` | Recent memories grouped by day (what happened this/last week) |
| 26 | `list_projects` | Workspaces with memory counts |
| 27 | `list_sources` | Which AI clients stored memories |
| 28 | `list_people` | People across your memories (calendar/email/transcript), by frequency |
| 29 | `about_person` | One person's profile + the memories mentioning them |
| 30 | `list_organizations` | Organizations across your memories (people grouped by email domain) |
| 31 | `about_organization` | One organization's profile + the memories mentioning it |
| 32 | `list_decisions` | Decisions detected in recent memories — what was decided, when/where |
| 33 | `list_fading_memories` | Review queue of memories about to be forgotten |
| 34 | `list_low_trust_memories` | Memories below a confidence threshold, least-trusted first |
| 35 | `list_review_memories` | Memories due for spaced-repetition review (fading, unpinned, un-snoozed) |
| 36 | `review_memory` | Apply a review decision — keep / reinforce / weaken / pin / forget / snooze |
| 37 | `create_session` | Start a named session |
| 38 | `end_session` | End session + consolidate its memories |
| 39 | `summarize_session` | Distill a session's memories into one durable summary memory |
| 40 | `consolidate_memories` | Promote short-term → episodic |
| 41 | `consolidate_similar` | Compress clusters of related aged memories into durable summaries (sleep-like) |
| 42 | `clear_short_term` | Clear the live FIFO deque |
| 43 | `dedupe_memories` | Find/remove near-duplicates (dry-run by default) |
| 44 | `generate_context_file` | Compile memories into CLAUDE.md / .cursorrules |
| 45 | `export_memories` | Export all memories as JSON |
| 46 | `import_memories` | Import memories from JSON |
| 47 | `create_backup` | Write a full backup (memories + sessions) to a file, optionally passphrase-encrypted |
| 48 | `restore_backup` | Restore memories + sessions from a backup file (merge or replace) |
| 49 | `reindex_entities` | Rebuild the persistent entity graph from all memories |
| 50 | `recompute_trust_scores` | Recompute provenance/trust scores for all memories |
| 51 | `detect_conflict_candidates` | Flag memory pairs that might disagree (shared entity + opposing pattern) |
| 52 | `review_conflict_candidate` | Human review — dismiss / confirm / keep-A / keep-B / both-valid |
| 53 | `list_mistakes` | Read the incident log back, newest first (filter by days / severity) |
| 54 | `evaluate_admission` | Preview the admission verdict for candidate text (admit/review/reject/redact) |
| 55 | `admit_memory` | Store through the admission gate — dedupe + secret redaction |
| 56 | `audit_secrets` | Scan stored memories for secrets (credentials, tokens) |
| 57 | `redact_secrets` | Strip secrets from stored memories (preview or apply) |
| 58 | `purge_memory` | Hard-delete a memory and report residue across tracked storage layers |
| 59 | `agent_connect` | Connect this agent to LEVH and create a tracking session |
| 60 | `agent_heartbeat` | Send a heartbeat to keep your agent connection alive |
| 61 | `agent_disconnect` | Disconnect this agent from LEVH |
| 62 | `create_checkpoint` | Save a checkpoint of your current work state |
| 63 | `list_agent_activity` | List recent agent activity — who connected, when, status |
| 64 | `list_checkpoints` | List recent checkpoints from agent sessions |
| 65 | `get_agent_stats` | Get aggregate statistics about your agent usage |
| 66 | `agent_metrics` | Get performance metrics for a specific agent |
| 67 | `usage_billing` | Get usage billing metrics for all agents |
| 68 | `project_collaboration` | Get collaboration info for agents on the same project |
| 69 | `list_connectors` | List available app connectors |
| 70 | `get_connector_help` | Get config help for a connector |
| 71 | `import_from_app` | Import from Calendar/Email/Transcripts/Notion/Obsidian/GitHub/local files |
| 72 | `sync_connector` | Connector v2 — fetch + gate-filtered incremental ingest |
| 73 | `connector_sync_status` | Per-source sync bookkeeping (last synced, totals, run count) |
