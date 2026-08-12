# Contributing

StackMemory is currently focused on release hardening and reliable local-first installation.

## Ground rules

- Keep changes narrow and testable.
- Do not add cloud, auth, billing, or workspace features without a design issue.
- Do not commit runtime artifacts such as `.env`, `stackmemory.db`, `.pytest_cache`, `.next`, `node_modules`, logs, or generated exports.
- Keep tests runnable without an OpenAI key and without `sentence-transformers`.
- Use `EMBEDDER_MODE=hash` for deterministic CI and smoke tests.

## Local validation

```bash
python -m pip install -e ".[dev]"
python -m compileall -q server tests
EMBEDDER_MODE=hash python -m pytest -q
EMBEDDER_MODE=hash python -m pytest -q tests/test_api_smoke.py
python -m build
twine check dist/*
```

Frontend validation:

```bash
cd frontend
npm ci
NEXT_TELEMETRY_DISABLED=1 npm run build
npm audit --omit=dev
```
