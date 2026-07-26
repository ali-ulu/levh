# CLI Reference

```bash
levh serve                     # API + dashboard on :8000
levh doctor                    # health checks
levh setup --status            # computed first-run readiness
levh setup --demo --client claude --profile work
levh setup --real --client cursor --profile minimal
levh seed-demo                 # load a demo corpus into an empty store
levh capture "note" --pin      # store a memory (auto-detects git repo as project)
levh context -o CLAUDE.md      # generate a context file from memories
levh hook install              # git post-commit auto-capture
levh summarize <session_id>    # distill a session into one summary memory
levh benchmark                 # recall-quality harness (hit@k / MRR)
levh tune                      # fit H(x,psi) weights offline; reports cross-validated gain
levh mcp config cursor         # print MCP config for a client
levh mcp stdio                 # run the MCP stdio server
levh eval run                  # golden-fixture memory evaluation → eval_report.json
levh eval report               # print the last written evaluation report
levh dogfood status            # aggregate view of the local usage journal
levh dogfood export -o out.json  # write the aggregate dogfood report (explicit)
```
