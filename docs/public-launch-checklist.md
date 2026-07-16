# Public Launch Checklist

- [ ] `main` is stable and clean.
- [ ] Backend tests pass with `EMBEDDER_MODE=hash`.
- [ ] API smoke passes with no hang.
- [ ] Built wheel installs in a clean virtual environment.
- [ ] `stackmemory benchmark` works from the installed wheel.
- [ ] `GET /` serves dashboard HTML from the installed wheel.
- [ ] Frontend production build exits cleanly.
- [ ] `npm audit --omit=dev` has no critical/high findings.
- [ ] Docker image build verified.
- [ ] Docker Compose startup verified.
- [ ] MCP stdio store/recall verified.
- [ ] MCP SSE documented only at the level actually verified.
- [ ] PyPI project name checked/reserved.
- [ ] TestPyPI publish verified.
- [ ] Public README quickstart tested on a clean machine.
- [ ] First screenshot/GIF prepared.
- [ ] Launch copy prepared.
