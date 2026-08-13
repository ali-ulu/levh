# CLI Reference

Every command, generated from the argparse definitions in
`server/cli_parsers.py` and locked by `tests/test_docs_match_code.py` — a new
subcommand cannot land here undocumented.

Run `levh <command> --help` for the flags of any one of them.

| Command | Description |
|---------|-------------|
| `levh doctor` | Check system health and dependencies |
| `levh setup` | First-run setup for demo or real data |
| `levh init` | Create local config directory and defaults |
| `levh serve` | Launch the API server |
| `levh capture` | Store a memory from the command line |
| `levh admit` | Store a memory through the admission gate (dedupe + secret redaction) |
| `levh sync` | Connector v2: gate-filtered incremental import |
| `levh context` | Generate CLAUDE.md / .cursorrules from memories |
| `levh summarize` | Distill a session into one summary memory |
| `levh benchmark` | Run the recall-quality benchmark (hit@k / MRR) |
| `levh tune` | Fit H(x,psi) weights to the labelled query set (offline) |
| `levh hook <sub>` | Git auto-capture hook |
| `levh hook install` | Install post-commit capture hook |
| `levh hook uninstall` | Remove the capture hook |
| `levh mcp <sub>` | MCP server commands |
| `levh mcp config` | Generate MCP client config JSON |
| `levh mcp init` | Scaffold a new MCP server project (optionally with LEVH memory) |
| `levh mcp profiles` | List MCP tool profiles and their tool counts |
| `levh mcp stdio` | Launch MCP stdio server |
| `levh eval <sub>` | Golden-fixture memory evaluation (offline, deterministic) |
| `levh eval run` | Run the evaluation and write the report |
| `levh eval report` | Print the last written evaluation report |
| `levh dogfood <sub>` | Local dogfood journal (local-only; export is explicit) |
| `levh dogfood status` | Aggregate stats from the local journal |
| `levh dogfood export` | Write the aggregate report to a file (explicit user action) |
| `levh review <sub>` | Spaced-repetition review of fading memories |
| `levh review list` | List memories due for review |
| `levh review apply` | Apply a review action to a memory |
| `levh audit-secrets` | Scan stored memories for secrets (credentials, tokens) |
| `levh redact-secrets` | Strip secrets from stored memories |
| `levh purge` | Hard-delete a memory and verify it's fully gone |
| `levh seed-demo` | Load a deterministic demo corpus so a first run has data to explore |
| `levh remove-demo` | Remove demo-tagged memories, leaving real data untouched |
| `levh continue` | Show context to resume work (session DNA) |
| `levh export-full` | Export memories, entity graph, trust scores, and conflicts to one file |
| `levh entities <sub>` | Persistent entity knowledge graph |
| `levh entities reindex` | Rebuild the entity graph from all memories |
| `levh entities list` | List entities |
| `levh entities about` | Show one entity's profile |
| `levh trust <sub>` | Provenance / trust score for memories |
| `levh trust show` | Show a memory's trust breakdown |
| `levh trust recompute` | Recompute all trust scores |
| `levh trust low` | List low-trust memories |
| `levh conflicts <sub>` | Deterministic conflict-candidate review |
| `levh conflicts detect` | Detect conflict candidates |
| `levh conflicts list` | List conflict candidates |
| `levh conflicts review` | Review a candidate |

## The ones worth knowing first

```bash
levh setup --demo --client claude --profile work   # first run, with a demo corpus
levh serve                                         # API + dashboard on :8000
levh capture "Atlas uses PostgreSQL in production" # store a memory
levh context -o CLAUDE.md                          # compile memory into a context file
levh hook install                                  # capture every git commit message
levh mcp config cursor                             # print MCP config for a client
levh mcp init my-server --with-memory              # scaffold a server on this database
```
