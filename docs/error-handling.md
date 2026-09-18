# Error handling policy

LEVH runs offline, in a user's own process, and often in the background
(MCP stdio server, checkpoint scheduler, connector sync). A crash there is
worse than a degraded answer, so some failure paths are *supposed* to absorb
an exception instead of propagating it. This page says which ones, and how to
mark them so the next reader does not have to guess.

The rules below are enforced by ruff (`BLE001`, `ARG` in `.ruff.toml`); the
prose exists to explain the intent behind the `# noqa` comments.

## `except Exception` is opt-in

Catching everything is allowed only when **all three** hold:

1. **The failure must not escape.** The call is a boundary (HTTP route,
   CLI command, background loop, plugin hook) or genuinely best-effort
   (telemetry, warm-up, cleanup, optional metadata).
2. **The failure is not silently lost.** It is logged (`logger.exception`),
   converted into a user-visible result (FAIL line, error message, exit
   code, degraded fallback value), or counted as an error for the caller.
   `except Exception: pass` is never acceptable.
3. **It says why, on the line.** The `except` carries a
   `# noqa: BLE001 - <reason>` comment naming the specific thing being
   tolerated, in one line.

```python
try:
    await refresh_presence_cache()
except Exception:  # noqa: BLE001 - presence tracking must never block server startup
    logger.exception("presence refresh failed")
```

Annotating without a reason defeats the rule; the comment is the review
artifact. If the reason does not fit on one line, the catch is probably too
wide and should be narrowed instead.

## Prefer the narrowest thing that is still correct

Catch the exception you can actually name first: `ValueError`, `KeyError`,
`OSError`, `json.JSONDecodeError`, `httpx.HTTPError`, a project exception.
A broad catch is the fallback for "unknown third-party or plugin code", not a
shortcut for "I did not look up which exception this raises".

`except BaseException:` and bare `except:` stay banned outright: they also
swallow `KeyboardInterrupt` and `SystemExit`, which are user intent, not
failures.

## `ARG`: unused arguments are a smell

An unused argument usually means one of three things, and ruff's `ARG` family
forces that to be a decision rather than an accident:

- **Dead plumbing** — the parameter no longer has a caller that passes
  anything meaningful. Delete it and the call site.
- **A broken override** — a subclass stopped using a parameter the base
  contract defines. Either use it or make the divergence explicit.
- **A deliberate interface match** — the signature is fixed by a framework,
  a callback contract, or a test double. Rename the parameter with a leading
  underscore (`_request`, `_frame`) so the intent is visible at the
  definition site.

Two narrow exemptions live in `.ruff.toml`:

- `tests/**` ignores `ARG` entirely: fake connectors, stub callbacks and
  `monkeypatch` stand-ins exist to *match* a signature, and the body has no
  reason to read it — that is what makes it a double.
- `ignore-variadic-names = true` exempts `**kwargs` itself. The connector
  `fetch()` overrides are called as `conn.fetch(**params)`; renaming a
  variadic the interface owns would obscure the contract, while a *named*
  unused argument is still flagged.

## Turning the rules on

`BLE001` and `ARG` were not enabled all at once on an unreviewed codebase.
Every existing hit was read first, and each one had to earn its exemption:
the 41 `except Exception` sites in `server/` are boundaries, best-effort
paths, or documented degradations that log, return a visible error, or fall
back to a weaker result, so each carries a one-line reason. Anything that
could not be justified that way would have been narrowed or removed rather
than annotated — and new code must follow the rules above, because a
violation is a lint failure, not a review comment.
