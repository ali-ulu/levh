# MCP Tools

The complete MCP tool surface. See the [README](../README.md) for an overview.

> **Tool profiles.** Advertising all 59 tools to a client hurts tool-selection
> accuracy, so LEVH groups them into cumulative profiles —
> `minimal` (5) ⊂ `work` (15) ⊂ `admin` (54) ⊂ `full` (59). Generated configs
> default to **`work`**; run `levh mcp profiles` to see the bands, or set
> `LEVH_MCP_PROFILE` / `mcp config --profile <name>` to change it. The
> full table below is the `full` surface. Profiles only filter which tools a
> client sees — they are **not** an authentication or authorization boundary;
> every profile talks to the same engine instance with the same access.

| # | Tool | Description |
|---|------|-------------|
| 1 | `store_memory` | Store a memory with importance, tags, project, source, pin |
| 2 | `recall_memory` | Recall memories ranked by H(x,ψ) score (filter by session/project) |
| 3 | `forget_memory` | Delete from all 3 layers |
| 4 | `search_memory` | Semantic search with detailed results |
| 5 | `update_memory` | Update content/importance/tags |
| 6 | `list_memories` | List with type/tag/session/project/source/pinned filters |
| 7 | `get_memory_stats` | System statistics and metrics |
| 8 | `consolidate_memories` | Promote short-term → episodic |
| 9 | `clear_short_term` | Clear the live FIFO deque |
| 10 | `set_importance` | Set importance (0.0-1.0) |
| 11 | `get_context` | Build context window (short-term + pinned + important) |
| 12 | `create_session` | Start a named session |
| 13 | `end_session` | End session + consolidate its memories |
| 14 | `export_memories` | Export all memories as JSON |
| 15 | `import_memories` | Import memories from JSON |
| 16 | `import_from_app` | Import from Calendar/Email/Transcripts/Notion/Obsidian/GitHub/local files |
| 17 | `list_connectors` | List available app connectors |
| 18 | `get_connector_help` | Get config help for a connector |
| 19 | `pin_memory` | Pin a memory — exempt from decay, always in context files |
| 20 | `unpin_memory` | Restore normal decay |
| 21 | `list_projects` | Workspaces with memory counts |
| 22 | `list_sources` | Which AI clients stored memories |
| 23 | `generate_context_file` | Compile memories into CLAUDE.md / .cursorrules |
| 24 | `dedupe_memories` | Find/remove near-duplicates (dry-run by default) |
| 25 | `reinforce_memory` | Manually strengthen a memory — resets decay clock, grows stability |
| 26 | `memory_feedback` | helpful=true reinforces; helpful=false makes wrong/stale info fade fast |
| 27 | `list_fading_memories` | Review queue of memories about to be forgotten |
| 28 | `related_memories` | Nearest-neighbour "see also" for a memory (live, embedding-based) |
| 29 | `summarize_session` | Distill a session's memories into one durable summary memory |
| 30 | `ask_memory` | Ask your memory a question → synthesized, cited answer (read-only) |
| 31 | `list_people` | People across your memories (calendar/email/transcript), by frequency |
| 32 | `about_person` | One person's profile + the memories mentioning them |
| 33 | `timeline` | Recent memories grouped by day (what happened this/last week) |
| 34 | `briefing` | Daily briefing — today's events, open commitments, and what you may be forgetting |
| 35 | `list_organizations` | Organizations across your memories (people grouped by email domain) |
| 36 | `about_organization` | One organization's profile + the memories mentioning it |
| 37 | `list_decisions` | Decisions detected in recent memories — what was decided, when/where |
| 38 | `create_backup` | Write a full backup (memories + sessions) to a file, optionally passphrase-encrypted |
| 39 | `restore_backup` | Restore memories + sessions from a backup file (merge or replace) |
| 40 | `meeting_prep` | Pre-meeting brief — next meeting, attendees, what you last discussed, open items |
| 41 | `consolidate_similar` | Compress clusters of related aged memories into durable summaries (sleep-like) |
| 42 | `list_review_memories` | Memories due for spaced-repetition review (fading, unpinned, un-snoozed) |
| 43 | `review_memory` | Apply a review decision — keep / reinforce / weaken / pin / forget / snooze |
| 44 | `evaluate_admission` | Preview the admission verdict for candidate text (admit/review/reject/redact) |
| 45 | `admit_memory` | Store through the admission gate — dedupe + secret redaction |
| 46 | `sync_connector` | Connector v2 — fetch + gate-filtered incremental ingest |
| 47 | `connector_sync_status` | Per-source sync bookkeeping (last synced, totals, run count) |
| 48 | `audit_secrets` | Scan stored memories for secrets (credentials, tokens) |
| 49 | `redact_secrets` | Strip secrets from stored memories (preview or apply) |
| 50 | `purge_memory` | Hard-delete a memory and report residue across tracked storage layers |
| 51 | `reindex_entities` | Rebuild the persistent entity graph from all memories |
| 52 | `list_entities` | List graph entities by type, most-mentioned first |
| 53 | `about_entity` | One entity's profile — its memories and co-occurring entities |
| 54 | `memory_trust` | A memory's provenance/trust breakdown (confidence + explainable evidence) |
| 55 | `recompute_trust_scores` | Recompute provenance/trust scores for all memories |
| 56 | `list_low_trust_memories` | Memories below a confidence threshold, least-trusted first |
| 57 | `detect_conflict_candidates` | Flag memory pairs that might disagree (shared entity + opposing pattern) |
| 58 | `list_conflict_candidates` | List conflict candidates by status (open/confirmed/…) |
| 59 | `review_conflict_candidate` | Human review — dismiss / confirm / keep-A / keep-B / both-valid |
