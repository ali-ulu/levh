## What changed

<!-- One paragraph. What the diff does, not why it is good. -->

## Why

<!-- The problem this solves. Link the issue: `Closes #123`. -->

## How it was verified

<!--
Paste the commands you ran and what they printed. "Tests pass" is not
evidence; `EMBEDDER_MODE=hash python -m pytest -q tests/test_x.py` plus its
summary line is. A reviewer should be able to repeat this.
-->

```
EMBEDDER_MODE=hash python -m pytest -q
python -m ruff check .
```

## Risk

<!--
What could this break, and who would notice? Call out touched boundaries:
storage format, migrations, auth, API shape, CI permissions. If it changes
behavior a user depends on, say whether it is backward compatible.

Say "none — docs only" when that is true; it is a useful answer.
-->

## Checklist

- [ ] Change is scoped to the linked issue; no unrelated reformatting
- [ ] Tests added or updated for the behavior change (or: none needed, and I said why above)
- [ ] Local gates green: `EMBEDDER_MODE=hash python -m pytest -q` and `python -m ruff check .`
- [ ] Frontend gates run if `frontend/` changed: `npm ci && npm run build`
- [ ] No secrets, runtime artifacts, or generated exports committed
